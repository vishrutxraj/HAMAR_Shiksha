"""Demonstration dataset - every row is flagged `is_demo` and every school
code lies in the reserved demo district range (2260-2299), so nothing here
can be mistaken for, or collide with, official UDISE+ data.

Dashboard figures are not hard-coded: the seed creates raw attendance and
assessment records and the normal analytics pipeline aggregates them.
Refuses to run when HS_ALLOW_DEMO_DATA is false.
"""
from __future__ import annotations

import io
import csv
import random
import re
from collections import defaultdict
from datetime import date, datetime, time, timedelta

from pathlib import Path

from sqlalchemy import insert, select
from sqlalchemy.orm import Session

from app import geo
from app.config import get_settings
from app.demo_content import (
    BANK,
    DESCRIPTIVE_QUESTIONS,
    FIRST_NAMES,
    LAST_NAMES,
    LO_TEXT,
    SHORT_QUESTIONS,
    SUBJECTS_BY_BAND,
    TRAINING,
    band,
    lo_code,
    questions_for,
)
from app.kb.ingest import ingest_manifest
from app.models import (
    Doubt,
    DoubtReply,
    RevisionItem,
    StudentGoal,
    StudyPair,
    AcademicCalendar,
    CurriculumDocument,
    LearnerCompetency,
    LearnerEvent,
    LearningOutcome,
    LessonPlan,
    TeacherTrainingModule,
    TeachingMilestone,
    TrainingCompletion,
    BLOCK_ADMIN,
    DISTRICT_ADMIN,
    STATE_ADMIN,
    STUDENT,
    TEACHER,
    Assessment,
    AssessmentResult,
    Assignment,
    AttendanceRecord,
    Block,
    ClassSection,
    District,
    LearningActivity,
    LearningProgress,
    School,
    StudentEnrollment,
    StudentProfile,
    Subject,
    TeacherClassAssignment,
    TeacherProfile,
    TimetableSlot,
    User,
    UserRoleAssignment,
    utcnow,
)
from app.audit import audit
from app.registry import import_registry_csv
from app.security import hash_password
from app.services import analytics, insights, learner, pariksha, planning
from app.services.accounts import role_id

DEMO_PASSWORDS = {
    "state": "Demo@State2026",
    "district": "Demo@District2026",
    "block": "Demo@Block2026",
    "teacher": "Demo@Teacher2026",
    "student": "Demo@Student26",
}

# Real district names (app/geo.py) with codes in the reserved demo range. The four
# "core" districts keep stable codes (tests and the walkthrough rely on them) and use
# real block names; every other district gets one demo block. All schools, people
# and numbers are fictional.
CORE_GEO = {
    "Raipur": ("2291", [("229101", "Dharsiwa", "धरसींवा"), ("229102", "Abhanpur", "अभनपुर")]),
    "Durg": ("2292", [("229201", "Durg", "दुर्ग"), ("229202", "Patan", "पाटन")]),
    "Bilaspur": ("2293", [("229301", "Bilha", "बिल्हा"), ("229302", "Takhatpur", "तखतपुर")]),
    "Bastar": ("2294", [("229401", "Jagdalpur", "जगदलपुर")]),
}


def _build_geo() -> list[tuple]:
    """[(district_code, name, name_hi, [(block_code, name, name_hi)], core?)] for all 33 districts."""
    out, next_code = [], 60
    for d in geo.DISTRICTS:
        if d.name in CORE_GEO:
            code, blocks = CORE_GEO[d.name]
            out.append((code, d.name, d.name_hi, blocks, True))
        else:
            code = f"22{next_code}"
            next_code += 1
            out.append((code, d.name, d.name_hi, [(code + "01", f"{d.name} (demo block)", f"{d.name_hi} (डेमो ब्लॉक)")],
                        False))
    return sorted(out, key=lambda g: (not g[4], g[0]))  # core districts first (stable numbering)


GEO = _build_geo()
SCHOOLS_PER_BLOCK = 4          # onboarded schools per core block
MONTHS = 4
STUDENTS_PER_CLASS = 12        # core districts
STUDENTS_PER_CLASS_OTHER = 6   # other districts (>= analytics.MIN_GROUP so class views are not suppressed)
SHOWCASE_CODE = "22910100101"


def district_slug(name: str) -> str:
    return re.sub(r"[^a-z]+", "-", name.lower()).strip("-")


def subjects_for(grade: int) -> list[str]:
    """Classes 1-2: Hindi, English, Maths; 3-5 add EVS; 6-8: middle-school subjects."""
    return SUBJECTS_BY_BAND[band(grade)] if grade >= 3 else ["HIN", "ENG", "MATH"]


def _school_plan(scale: str) -> dict[str, tuple[str, int, int]]:
    """official code -> (category, lowest class, highest class) for every onboarded school."""
    plan = {}
    for d_code, d_name, _, blocks, core in GEO:
        for b_code, _, _ in blocks:
            if core:
                for n in range(1, SCHOOLS_PER_BLOCK + 1):
                    code = f"{b_code}001{n:02d}"
                    if code == SHOWCASE_CODE:
                        plan[code] = ("Composite (1-8)", 1, 8)
                    elif n % 2:
                        plan[code] = ("Primary", 1, 5)
                    else:
                        plan[code] = ("Upper Primary", 6, 8)
            else:
                plan[f"{b_code}00101"] = ("Composite (1-8)", 1, 8)
                if scale == "full":
                    plan[f"{b_code}00102"] = ("Primary", 1, 5)
    return plan


def grades_for(code: str, lo: int, hi: int, scale: str) -> tuple[int, ...]:
    if scale == "full":
        return tuple(range(lo, hi + 1))
    # compact (tests): the original small footprint, two classes per school
    if code == SHOWCASE_CODE or (lo, hi) == (1, 8):
        return (5, 8)
    return (3, 5) if hi <= 5 else (6, 8)


class DemoDisabled(RuntimeError):
    pass


def _registry_csv(plan: dict, rng: random.Random) -> str:
    """Demo registry extract: the schools to onboard, codes present in the
    registry but not yet onboarded (a different number per district, so the
    map has something to compare) and one closed code."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["udise_code", "school_name", "district_code", "district_name", "district_name_hi", "block_code",
                "block_name", "block_name_hi", "locality", "category", "management", "status"])
    for d_code, d_name, d_hi, blocks, core in GEO:
        for b_code, b_name, b_hi in blocks:
            short = b_name.replace(" (demo block)", "")
            row = lambda code, kind, n, status="active": w.writerow([  # noqa: E731
                code, f"Govt. {kind.split(' (')[0]} School {short}-{n} (Demo)", d_code, d_name, d_hi, b_code, b_name,
                b_hi, f"Demo Village {short}-{n}", kind, "Department of Education", status])
            for code, (kind, _, _) in plan.items():
                if code.startswith(b_code):
                    row(code, kind, int(code[-2:]))
            # Codes present in the registry but not yet onboarded:
            if core:
                for n in (8, 9):
                    row(f"{b_code}001{n:02d}", "Primary", n)
            for n in range(1, rng.randint(12, 48) + 1):
                row(f"{b_code}002{n:02d}", "Primary" if n % 3 else "Upper Primary", 100 + n)
    w.writerow(["22910100199", "Govt. Primary School Dharsiwa-99 (Demo, closed)", "2291", "Raipur", "रायपुर",
                "229101", "Dharsiwa", "धरसींवा", "Demo Village Dharsiwa-99", "Primary", "Department of Education",
                "closed"])
    return buf.getvalue()


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def seed_demo(db: Session, *, today: date | None = None) -> dict:
    if not get_settings().allow_demo_data:
        raise DemoDisabled("Demo data is disabled (HS_ALLOW_DEMO_DATA=false)")
    if db.scalar(select(School.id).where(School.official_code == SHOWCASE_CODE)) is not None:
        return {"skipped": "demo data already present"}

    rng = random.Random(2026)
    today = today or date.today()
    subjects = {s.code: s for s in db.scalars(select(Subject))}
    hashes = {k: hash_password(v) for k, v in DEMO_PASSWORDS.items()}
    roles = {name: role_id(db, name) for name in (STATE_ADMIN, DISTRICT_ADMIN, BLOCK_ADMIN, TEACHER, STUDENT)}

    def user(login_id: str, name: str, kind: str, role: str, **scope) -> User:
        u = User(login_id=login_id, full_name=name, password_hash=hashes[kind], is_demo=True)
        db.add(u)
        db.flush()
        db.add(UserRoleAssignment(user_id=u.id, role_id=roles[role], **scope))
        return u

    # --- registry + geography (through the normal import path) ---
    scale = get_settings().demo_scale
    plan = _school_plan(scale)
    import_registry_csv(db, _registry_csv(plan, rng), source="DEMO", source_reference="Generated demonstration extract",
                        imported_by=None, demo=True)
    districts = {d.code: d for d in db.scalars(select(District))}
    blocks = {b.code: b for b in db.scalars(select(Block))}

    # --- administrators: state, one per district, one block officer ---
    state = user("state.admin", "Demo State Administrator", "state", STATE_ADMIN)
    for d_code, d_name, _, _, _ in GEO:
        user(f"district.{district_slug(d_name)}", f"Demo District Officer ({d_name})", "district", DISTRICT_ADMIN,
             district_id=districts[d_code].id)
    user("block.dharsiwa", "Demo Block Officer (Dharsiwa)", "block", BLOCK_ADMIN,
         district_id=districts["2291"].id, block_id=blocks["229101"].id)

    # --- schools ---
    period_list = analytics.recent_periods(MONTHS, today)
    window_start = analytics.period_bounds(period_list[0])[0]
    all_codes = list(plan)
    core_codes = {c for c in all_codes if c[:4] in {g[0] for g in GEO if g[4]}}
    # Reporting profiles chosen to exercise every data status.
    profile = {c: "current" for c in all_codes}
    for c in ("22920200103", "22930100104"):
        profile[c] = "stale"
    for c in ("22930200102", "22940100103"):
        profile[c] = "missing"
    profile["22920100104"] = "pending"

    from app.models import SchoolCodeRegistry
    teacher_no = 0
    counters = defaultdict(int)
    progress: dict[tuple[int, int, str], list[int]] = defaultdict(lambda: [0, 0])
    competencies: dict[tuple[int, str], list] = defaultdict(lambda: [0, 0, []])
    events: list[dict] = []
    attendance_rows: list[dict] = []
    result_rows: list[dict] = []
    revision_rows: dict[tuple[int, str], dict] = {}
    all_classes: list[tuple[ClassSection, str, date | None]] = []
    showcase_classes: list[ClassSection] = []
    rng_att = random.Random(4242)
    demo_students: dict[str, User] = {}
    district_logins: dict[str, dict] = {}
    academic_year = f"{today.year if today.month >= 4 else today.year - 1}-" \
                    f"{str((today.year if today.month >= 4 else today.year - 1) + 1)[2:]}"

    for code in all_codes:
        entry = db.get(SchoolCodeRegistry, code)
        b = blocks[entry.block_code]
        showcase = code == SHOWCASE_CODE
        core = code in core_codes
        _, lo, hi = plan[code]
        school = School(official_code=code, name=entry.school_name, district_id=b.district_id, block_id=b.id,
                        locality=entry.locality, locality_type="urban" if code.endswith("04") else "rural",
                        category=entry.category, medium="Hindi", lowest_class=lo, highest_class=hi,
                        status="pending" if profile[code] == "pending" else "active", created_by=state.id,
                        activated_by=None if profile[code] == "pending" else state.id,
                        activated_at=None if profile[code] == "pending" else utcnow(), is_demo=True)
        db.add(school)
        db.flush()
        counters["schools"] += 1
        if profile[code] == "pending":
            continue

        quality = rng.uniform(-0.16, 0.12)
        att_base = rng.uniform(0.62, 0.93)
        t_att_base = rng.uniform(0.78, 0.98)
        if showcase:
            quality, att_base, t_att_base = 0.0, 0.86, 0.95
        end = {"current": today, "stale": today - timedelta(days=70), "missing": None}[profile[code]]

        grades = grades_for(code, lo, hi, scale)
        per_class = STUDENTS_PER_CLASS if core else STUDENTS_PER_CLASS_OTHER
        # Teachers: one for languages/social science, one for maths/science/EVS (+ head teacher flag on the first).
        teachers = []
        for i in range(2):
            teacher_no += 1
            emp = f"DEMO-T-{teacher_no:03d}"
            name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
            t = user(emp.lower(), name, "teacher", TEACHER, school_id=school.id)
            quals = ["HIN", "ENG"] if i == 0 else (["MATH", "SCI"] if rng.random() < 0.4 and not showcase else ["MATH"])
            db.add(TeacherProfile(user_id=t.id, employee_id=emp, school_id=school.id,
                                  designation="Head Teacher" if i == 0 else "Assistant Teacher",
                                  is_head_teacher=i == 0, verification_status="verified",
                                  verified_by=state.id, verified_at=utcnow(), subject_qualifications=quals))
            teachers.append(t)
            counters["teachers"] += 1
        d_name = next(g[1] for g in GEO if g[0] == code[:4])
        district_logins.setdefault(d_name, {"district_code": code[:4], "teacher": teachers[0].login_id.upper(),
                                            "school": school.name})
        # The showcase school gets a third teacher still awaiting verification.
        if showcase:
            teacher_no += 1
            pend = user(f"demo-t-{teacher_no:03d}", "Demo Pending Teacher", "teacher", TEACHER, school_id=school.id)
            db.add(TeacherProfile(user_id=pend.id, employee_id=f"DEMO-T-{teacher_no:03d}", school_id=school.id,
                                  designation="Assistant Teacher", verification_status="pending"))

        school_days = []
        if end is not None:
            d = window_start
            while d <= end:
                if d.weekday() in (0, 2, 4):
                    school_days.append(d)
                d += timedelta(days=1)
        for t in teachers:
            for d in school_days:
                attendance_rows.append(dict(person_user_id=t.id, person_type="teacher", school_id=school.id,
                                            class_section_id=None, date=d,
                                            status="present" if rng.random() < t_att_base else "absent",
                                            recorded_at=utcnow()))

        for grade in grades:
            cs = ClassSection(school_id=school.id, grade=grade, section="A", academic_year=academic_year)
            db.add(cs)
            db.flush()
            all_classes.append((cs, profile[code], end))
            if showcase:
                showcase_classes.append(cs)
            subj_codes = subjects_for(grade)
            for sc in subj_codes:
                t = teachers[0] if sc in ("HIN", "ENG", "SST") else teachers[1]
                db.add(TeacherClassAssignment(teacher_user_id=t.id, class_section_id=cs.id,
                                              subject_id=subjects[sc].id,
                                              is_class_teacher=(sc == "MATH"), approved_by=state.id,
                                              approved_at=utcnow()))
            if showcase:
                for day in range(6):
                    for period, sc in enumerate(subj_codes, start=1):
                        t = teachers[0] if sc in ("HIN", "ENG", "SST") else teachers[1]
                        db.add(TimetableSlot(class_section_id=cs.id, subject_id=subjects[sc].id,
                                             teacher_user_id=t.id, day_of_week=day,
                                             period=(period + day) % len(subj_codes) + 1))

            # Students
            students = []
            for i in range(per_class):
                fixed = None
                if showcase and grade == 5 and i == 0:
                    fixed = "st-demo01"
                elif showcase and grade == 8 and i == 0:
                    fixed = "st-demo02"
                elif not core and grade == 5 and i == 0 and d_name not in demo_students:
                    fixed = f"st-{district_slug(d_name)}"   # one predictable student login per district
                login = fixed or "st-" + "".join(rng.choice("abcdefghjkmnpqrstuvwxyz23456789") for _ in range(7))
                name = f"{rng.choice(FIRST_NAMES)} {rng.choice(LAST_NAMES)}"
                s = user(login, name, "student", STUDENT, school_id=school.id)
                db.add(StudentProfile(user_id=s.id, school_id=school.id, gender=rng.choice(["female", "male"]),
                                      birth_year=today.year - grade - 6, guardian_name=f"Guardian of {name}",
                                      guardian_consent=True, consent_recorded_at=utcnow(),
                                      consent_recorded_by=teachers[0].id))
                db.add(StudentEnrollment(student_user_id=s.id, class_section_id=cs.id, academic_year=academic_year,
                                         roll_no=i + 1, enrolled_by=teachers[0].id))
                ability = _clamp(rng.gauss(0.70 + quality, 0.12), 0.2, 0.98)
                presence = _clamp(att_base + rng.gauss(0, 0.07), 0.3, 0.99)
                if fixed == "st-demo01":
                    ability, presence = 0.72, 0.9
                students.append((s, ability, presence))
                if fixed:
                    demo_students[fixed if fixed.startswith("st-demo") else d_name] = s
                    if not fixed.startswith("st-demo"):
                        district_logins[d_name]["student"] = fixed
                counters["students"] += 1

            # A few children whose attendance falls away or stops (separate generator so the
            # rest of the demo data is unchanged): these exercise the attendance follow-up.
            pattern = {}
            for idx, (s, _, _) in enumerate(students):
                if s.login_id.startswith("st-demo"):
                    continue
                if showcase and grade == 5 and idx in (3, 5):
                    pattern[s.id] = "stopped" if idx == 3 else "falling"
                elif rng_att.random() < 0.05:
                    pattern[s.id] = rng_att.choice(["falling", "falling", "stopped"])
            stop_from = school_days[-5] if len(school_days) >= 5 else None
            fall_from = today - timedelta(days=28)
            for d in school_days:
                for s, _, presence in students:
                    r = rng.random()
                    status = "present" if r < presence else ("late" if r < presence + 0.03 else "absent")
                    pat = pattern.get(s.id)
                    if pat == "stopped" and stop_from and d >= stop_from:
                        status = "absent"
                    elif pat == "falling" and d >= fall_from and rng_att.random() < 0.6:
                        status = "absent"
                    attendance_rows.append(dict(person_user_id=s.id, person_type="student", school_id=school.id,
                                                class_section_id=cs.id, date=d, status=status, recorded_at=utcnow()))
                    events.append(dict(event_uid=f"demo-att-{s.id}-{d.isoformat()}", student_user_id=s.id,
                                       school_id=school.id, class_section_id=cs.id, event_type="attendance",
                                       payload={"date": d.isoformat(), "status": status,
                                                "summary": f"Attendance {d.isoformat()}: {status}"},
                                       occurred_at=datetime.combine(d, time(9)), recorded_at=utcnow(),
                                       source="import"))
                    counters["attendance"] += 1

            # Monthly unit tests per subject, generated from the bank.
            if end is None:
                continue
            for m_idx, period in enumerate(period_list):
                p_start, p_end = analytics.period_bounds(period)
                last_day = min(p_end - timedelta(days=1), end)
                if last_day < p_start:
                    continue
                for sc in subj_codes:
                    topics = list(BANK[(sc, band(grade))])
                    topic = topics[m_idx % len(topics)]
                    qs = questions_for(sc, grade, topic)
                    diff = BANK[(sc, band(grade))][topic]["difficulty"]
                    t = teachers[0] if sc in ("HIN", "ENG", "SST") else teachers[1]
                    when = p_start + timedelta(days=rng.randint(0, (last_day - p_start).days))
                    a = Assessment(class_section_id=cs.id, subject_id=subjects[sc].id, created_by=t.id,
                                   title=f"{subjects[sc].name} unit test - {topic}", topic=topic, kind="unit_test",
                                   max_score=float(len(qs)), questions=qs, published=True,
                                   created_at=datetime.combine(when, time(9)))
                    db.add(a)
                    db.flush()
                    for s, ability, presence in students:
                        if rng.random() > presence + 0.05:
                            continue  # absent on test day
                        answers, correct, items = {}, 0, []
                        for q in qs:
                            ok = rng.random() < _clamp(ability - diff + rng.gauss(0, 0.05), 0.05, 0.98)
                            wrong = [i for i in range(len(q["options"])) if i != q["answer_index"]]
                            answers[q["id"]] = q["answer_index"] if ok else rng.choice(wrong)
                            correct += ok
                            items.append({"id": q["id"], "qtype": "mcq", "response": answers[q["id"]],
                                          "score": float(ok), "max": 1.0, "method": "mcq", "confidence": 1.0,
                                          "needs_review": False, "lo": q["lo"], "topic": topic})
                            if not ok and s.login_id in ("st-demo01", "st-demo02") and m_idx == len(period_list) - 1:
                                key = f"assessment:{a.id}:{q['id']}"
                                revision_rows[(s.id, key)] = dict(
                                    student_user_id=s.id, item_key=key, subject_id=subjects[sc].id, topic=topic,
                                    question={"text": q["text"], "options": q["options"],
                                              "answer_index": q["answer_index"]},
                                    times_wrong=1, correct_streak=0, mastered=False,
                                    next_review=today - timedelta(days=rng.randint(0, 3)), created_at=utcnow())
                        submitted = datetime.combine(when, time(11))
                        result_rows.append(dict(assessment_id=a.id, student_user_id=s.id, score=float(correct),
                                                max_score=float(len(qs)), answers=answers, item_results=items,
                                                topic_breakdown={topic: [correct, len(qs)]}, status="final",
                                                submitted_at=submitted))
                        uid = f"demo-res-{a.id}-{s.id}"
                        events.append(dict(event_uid=uid, student_user_id=s.id, school_id=school.id,
                                           class_section_id=cs.id, event_type="assessment_result",
                                           payload={"assessment_id": a.id, "subject_id": subjects[sc].id,
                                                    "score": correct, "max_score": len(qs),
                                                    "summary": f"{a.title}: {correct}/{len(qs)}",
                                                    "items": [{"lo": qs[0]["lo"], "score": correct, "max": len(qs),
                                                               "topic": topic}]},
                                           occurred_at=submitted, recorded_at=utcnow(), source="import"))
                        comp = competencies[(s.id, qs[0]["lo"])]
                        comp[0] += correct
                        comp[1] += len(qs)
                        comp[2].append(uid)
                        acc = progress[(s.id, subjects[sc].id, topic)]
                        acc[0] += correct
                        acc[1] += len(qs)
                        counters["results"] += 1
        db.flush()

    # Bulk inserts: these tables hold most of the generated rows.
    for rows, model in ((attendance_rows, AttendanceRecord), (result_rows, AssessmentResult),
                        (list(revision_rows.values()), RevisionItem)):
        for i in range(0, len(rows), 5000):
            db.execute(insert(model), rows[i:i + 5000])

    for (sid, subj_id, topic), (c, n) in progress.items():
        db.add(LearningProgress(student_user_id=sid, subject_id=subj_id, topic=topic, correct=c, attempted=n,
                                mastery=round(c / n, 4), last_activity_at=utcnow()))

    # --- curriculum, outcomes, question bank (Pariksha) ---
    lo_ids = _seed_curriculum(db, subjects, state.id)
    for i in range(0, len(events), 5000):
        db.execute(insert(LearnerEvent), events[i:i + 5000])
    for (sid, code), (c, n, uids) in competencies.items():
        m = round(c / n, 4)
        db.add(LearnerCompetency(student_user_id=sid, learning_outcome_id=lo_ids[code], correct=c, attempted=n,
                                 mastery=m, status=learner.competency_status(m, n), evidence=uids[-10:]))
    counters["learner_events"] = len(events)

    # --- approved learning content for every class 1-8 ---
    for grade in range(1, 9):
        for (sc, b_), topics in BANK.items():
            if b_ != band(grade) or sc not in subjects_for(grade):
                continue
            for topic, t in topics.items():
                lang = "hi" if sc == "HIN" else "en"
                db.add(LearningActivity(subject_id=subjects[sc].id, grade=grade, topic=topic, kind="lesson",
                                        title=f"{topic} - lesson", language=lang, content={"body": t["lesson"]},
                                        approved=True, approved_by=state.id, is_demo=True))
                db.add(LearningActivity(subject_id=subjects[sc].id, grade=grade, topic=topic, kind="exercise",
                                        title=f"{topic} - practice", language=lang,
                                        content={"questions": questions_for(sc, grade, topic)},
                                        approved=True, approved_by=state.id, is_demo=True))

    # --- open work in the showcase school ---
    for cs in showcase_classes:
        if cs.grade not in (5, 8):
            continue
        math_teacher = db.scalar(select(TeacherClassAssignment.teacher_user_id).where(
            TeacherClassAssignment.class_section_id == cs.id,
            TeacherClassAssignment.subject_id == subjects["MATH"].id))
        topic = "Fractions" if cs.grade <= 5 else "Linear equations"
        db.add(Assessment(class_section_id=cs.id, subject_id=subjects["MATH"].id, created_by=math_teacher,
                          title=f"{topic} practice quiz", topic=topic, kind="quiz",
                          max_score=float(len(questions_for("MATH", cs.grade, topic))),
                          questions=questions_for("MATH", cs.grade, topic), published=True,
                          due_date=today + timedelta(days=5)))
        db.add(Assignment(class_section_id=cs.id, subject_id=subjects["MATH"].id, created_by=math_teacher,
                          title=f"Find 5 examples of {topic.lower()} at home" if cs.grade <= 5
                          else "Write 3 word problems that use a linear equation",
                          description="Write them in your notebook and type a short summary here.",
                          kind="homework", topic=topic, due_date=today + timedelta(days=3)))
    db.flush()

    # --- Teacher Saathi: calendar, milestones, approved plans, micro-courses ---
    _seed_planning(db, rng, today, academic_year, subjects, state.id, all_classes, showcase_classes)

    # --- aggregates and recommendations through the normal pipeline ---
    analytics.recompute_snapshots(db, period_list)
    for cs in showcase_classes:
        for sid in db.scalars(select(StudentEnrollment.student_user_id).where(
                StudentEnrollment.class_section_id == cs.id)):
            insights.refresh_student_recommendations(db, sid)
        subj_ids = set(db.scalars(select(TeacherClassAssignment.subject_id).where(
            TeacherClassAssignment.class_section_id == cs.id)))
        insights.refresh_class_recommendations(db, cs, subj_ids)
    from app.models import AIRecommendation
    insights.refresh_school_interventions(db, list(db.scalars(select(School).where(School.status == "active"))))
    for rec in db.scalars(select(AIRecommendation)):
        rec.is_demo = True

    _seed_student_help(db, today, subjects, demo_students, showcase_classes)
    counters.update(_seed_digital_and_recognition(db, today, demo_students))

    audit(db, "demo.seeded", actor_user_id=None, actor_role="system", **counters)
    db.commit()
    write_demo_accounts(district_logins)
    return dict(counters)


def _seed_student_help(db: Session, today: date, subjects: dict, demo_students: dict, showcase_classes: list) -> None:
    """Self-improvement and peer-help examples for the Class 5 showcase student."""
    from app.services import studenthelp

    me = demo_students.get("st-demo01")
    c5 = next((c for c in showcase_classes if c.grade == 5), None)
    if me is None or c5 is None:
        return
    week = studenthelp.week_start(today)
    db.add(StudentGoal(student_user_id=me.id, kind="practice", title="Finish 3 practice sets", target=3, week_start=week))
    db.add(StudentGoal(student_user_id=me.id, kind="revision", title="Fix 5 of my mistakes", target=5, week_start=week))
    classmates = list(db.scalars(select(User).join(StudentEnrollment, StudentEnrollment.student_user_id == User.id)
                                 .where(StudentEnrollment.class_section_id == c5.id, User.id != me.id)
                                 .order_by(StudentEnrollment.roll_no)))
    teacher = db.scalar(select(TeacherClassAssignment.teacher_user_id).where(
        TeacherClassAssignment.class_section_id == c5.id, TeacherClassAssignment.subject_id == subjects["MATH"].id))
    asked = utcnow() - timedelta(days=2)
    d1 = Doubt(student_user_id=me.id, class_section_id=c5.id, subject_id=subjects["MATH"].id, audience="teacher",
               topic="Fractions", body="Why is 1/3 bigger than 1/5 when 5 is bigger than 3?", status="answered",
               created_at=asked, is_demo=True)
    d2 = Doubt(student_user_id=classmates[0].id, class_section_id=c5.id, subject_id=subjects["EVS"].id,
               audience="class", topic="Water", body="How can we save water at school?", status="answered",
               created_at=asked, is_demo=True)
    d3 = Doubt(student_user_id=classmates[1].id, class_section_id=c5.id, subject_id=subjects["MATH"].id,
               audience="class", topic="Measurement", body="How many centimetres make one metre?", status="open",
               created_at=utcnow() - timedelta(hours=5), is_demo=True)
    db.add_all([d1, d2, d3])
    db.flush()
    db.add(DoubtReply(doubt_id=d1.id, author_user_id=teacher, author_role="teacher", accepted=True, is_demo=True,
                      body="Cut one roti into 3 parts and another into 5. Each of the 3 pieces is bigger, because "
                           "the roti is shared among fewer people. So 1/3 > 1/5.", created_at=asked + timedelta(hours=3)))
    db.add(DoubtReply(doubt_id=d2.id, author_user_id=me.id, author_role="student", accepted=True, is_demo=True,
                      body="Close the tap after washing hands and use the leftover water for the school garden.",
                      created_at=asked + timedelta(hours=1)))
    db.add(StudyPair(class_section_id=c5.id, subject_id=subjects["MATH"].id, topic="Measurement",
                     helper_user_id=classmates[2].id, learner_user_id=me.id, created_by=teacher,
                     note="Practise converting metres and centimetres together for 15 minutes after lunch.",
                     is_demo=True))
    db.flush()


DEMO_ACCOUNTS_DOC = Path(__file__).resolve().parent.parent / "docs" / "DEMO_ACCOUNTS.md"


def _seed_digital_and_recognition(db: Session, today: date, demo_students: dict) -> dict:
    """Digital resources registered per school, their recent use, and a few
    appreciations. Separate random generator; all rows flagged is_demo."""
    from app.models import Appreciation, DigitalResource, DigitalUsage

    rng = random.Random(77)
    kinds = (("smart_class", "Smart class", 0.7, 15), ("ict_lab", "ICT lab", 0.5, 10),
             ("digital_library", "Digital library", 0.35, 6), ("tablets", "Tablet set", 0.25, 8),
             ("language_lab", "Language lab", 0.15, 5))
    rows: list[dict] = []
    n_res = 0
    los: dict[tuple[int, int], list[int]] = {}
    for lo_id, sid, g in db.execute(select(LearningOutcome.id, LearningOutcome.subject_id, LearningOutcome.grade)):
        los.setdefault((sid, g), []).append(lo_id)
    for school in db.scalars(select(School).where(School.status == "active").order_by(School.id)):
        classes = list(db.scalars(select(ClassSection).where(ClassSection.school_id == school.id)))
        if not classes:
            continue
        assigns = db.execute(select(TeacherClassAssignment.class_section_id, TeacherClassAssignment.subject_id,
                                    TeacherClassAssignment.teacher_user_id).where(
            TeacherClassAssignment.class_section_id.in_([c.id for c in classes]))).all()
        grade_of = {c.id: c.grade for c in classes}
        for kind, label, p, target in kinds:
            if rng.random() >= p:
                continue
            broken = rng.random() < 0.1
            r = DigitalResource(school_id=school.id, kind=kind, label=f"{label} (demo)", devices=rng.choice([1, 10, 20, 30]),
                                target_sessions_per_week=target, installed_year=rng.choice([2019, 2021, 2022, 2023]),
                                status="not_working" if broken else "working",
                                status_note="Projector lamp not working (demo)" if broken else "", is_demo=True)
            db.add(r)
            db.flush()
            n_res += 1
            if broken:
                continue
            use = 0.0 if rng.random() < 0.15 else rng.uniform(0.2, 1.1)
            for _ in range(int(target * 6 * use)):
                cid, sid, tid = rng.choice(assigns)
                lo = rng.choice(los.get((sid, grade_of[cid])) or [None]) if rng.random() < 0.7 else None
                rows.append(dict(resource_id=r.id, school_id=school.id, class_section_id=cid, subject_id=sid,
                                 learning_outcome_id=lo, teacher_user_id=tid,
                                 used_on=today - timedelta(days=rng.randint(0, 41)), minutes=rng.choice([35, 40, 45]),
                                 activity="Demo session", source="teacher", recorded_at=utcnow(), is_demo=True))
    for i in range(0, len(rows), 5000):
        db.execute(insert(DigitalUsage), rows[i:i + 5000])
    head = db.scalar(select(User).where(User.login_id == "demo-t-001"))
    st = demo_students.get("st-demo01")
    if head and st:
        db.add(Appreciation(recipient_user_id=st.id, giver_user_id=head.id, giver_role="teacher", category="improvement",
                            message="Your fractions work has improved a lot. Keep it up! (demo)", is_demo=True))
        block = db.scalar(select(User).where(User.login_id == "block.dharsiwa"))
        if block:
            db.add(Appreciation(recipient_user_id=head.id, giver_user_id=block.id, giver_role="block_admin",
                                category="milestones", message="Classes are on track with the plan. (demo)", is_demo=True))
    db.flush()
    return {"digital_resources": n_res, "digital_sessions": len(rows)}


def write_demo_accounts(district_logins: dict) -> None:
    """docs/DEMO_ACCOUNTS.md: one teacher and one student login per district (demo only)."""
    if not district_logins or get_settings().environment == "test":
        return
    by_div = defaultdict(list)
    for d in geo.DISTRICTS:
        if d.name in district_logins:
            by_div[d.division].append((d, district_logins[d.name]))
    lines = ["# Demo accounts by district", "",
             "Generated by `python -m app.cli seed-demo`. **Demonstration data only**: district names are real, but every "
             "school, person and number is fictional.", "",
             f"Passwords: district officers `{DEMO_PASSWORDS['district']}`, teachers `{DEMO_PASSWORDS['teacher']}`, "
             f"students `{DEMO_PASSWORDS['student']}`. State: `state.admin` / `{DEMO_PASSWORDS['state']}`.", ""]
    for code, name, name_hi in geo.DIVISIONS:
        lines += [f"## {name} division ({name_hi} संभाग)", "",
                  "| District | District officer | Teacher | Student | School |", "|---|---|---|---|---|"]
        for d, acc in by_div[code]:
            lines.append(f"| {d.name} ({d.name_hi}) | `district.{district_slug(d.name)}` | `{acc['teacher']}` | "
                         f"`{acc.get('student', 'st-demo01')}` | {acc['school']} |")
        lines.append("")
    try:
        DEMO_ACCOUNTS_DOC.write_text("\n".join(lines), encoding="utf-8")
    except OSError:  # container image: no docs/ folder and a read-only app directory; the data is already committed
        pass


DEMO_MANIFEST = Path(__file__).resolve().parent.parent / "data" / "demo_curriculum" / "manifest.json"


def _seed_curriculum(db: Session, subjects: dict, state_id: int) -> dict[str, int]:
    """Demo primers -> knowledge base; demo learning outcomes; demo question bank."""
    ingest_manifest(db, DEMO_MANIFEST, approved_by=state_id)
    docs = {(d.subject_id, d.grade): d.id for d in db.scalars(select(CurriculumDocument).where(
        CurriculumDocument.is_demo.is_(True), CurriculumDocument.language == "en"))}
    lo_ids: dict[str, int] = {}
    for grade in range(1, 9):
        for (sc, b_), topics in BANK.items():
            if b_ != band(grade) or sc not in subjects_for(grade):
                continue
            for idx, (topic, t) in enumerate(topics.items()):
                chapter, desc, desc_hi = LO_TEXT[topic]
                lo = LearningOutcome(code=lo_code(sc, grade, topic), subject_id=subjects[sc].id, grade=grade,
                                     chapter_no=idx + 1, chapter_title=chapter, topic=topic, description=desc,
                                     description_hi=desc_hi, planned_periods=8 if t["difficulty"] >= 0.2 else 6,
                                     curriculum_version="DEMO-2026",
                                     source_document_id=docs.get((subjects[sc].id, 5 if grade <= 5 else 8)),
                                     is_demo=True)
                db.add(lo)
                db.flush()
                lo_ids[lo.code] = lo.id
                for qi, q in enumerate(questions_for(sc, grade, topic)):
                    pariksha.create_question(db, {
                        "subject_id": subjects[sc].id, "grade": grade, "learning_outcome_id": lo.id, "topic": topic,
                        "qtype": "mcq", "text": q["text"], "options": q["options"], "correct_option": q["answer_index"],
                        "difficulty": 1 + qi % 3, "language": "hi" if sc == "HIN" else "en",
                        "misconceptions": q["misconceptions"]}, created_by=None, source="demo", status_="approved",
                        is_demo=True)
        for sc, b_, topic, text, accepted, diff in SHORT_QUESTIONS:
            if b_ == band(grade) and sc in subjects_for(grade):
                pariksha.create_question(db, {"subject_id": subjects[sc].id, "grade": grade, "topic": topic,
                                              "learning_outcome_id": lo_ids[lo_code(sc, grade, topic)], "qtype": "short",
                                              "text": text, "accepted_answers": accepted, "difficulty": diff},
                                         created_by=None, source="demo", status_="approved", is_demo=True)
        for sc, b_, topic, text, rubric, marks in DESCRIPTIVE_QUESTIONS:
            if b_ == band(grade) and sc in subjects_for(grade):
                pariksha.create_question(db, {"subject_id": subjects[sc].id, "grade": grade, "topic": topic,
                                              "learning_outcome_id": lo_ids[lo_code(sc, grade, topic)],
                                              "qtype": "descriptive", "text": text, "rubric": rubric,
                                              "max_marks": marks, "difficulty": 3},
                                         created_by=None, source="demo", status_="approved", is_demo=True)
    return lo_ids


def _seed_planning(db: Session, rng: random.Random, today: date, academic_year: str, subjects: dict, state_id: int,
                   all_classes: list, showcase_classes: list) -> None:
    from app.kb import retrieval
    from app.slm.gyan import _cite

    y = int(academic_year[:4])
    db.add(AcademicCalendar(
        academic_year=academic_year, district_id=None, start_date=date(y, 6, 16), end_date=date(y + 1, 4, 30),
        holidays=[{"from": f"{y}-11-07", "to": f"{y}-11-12", "name": "Deepawali break (demo dates)"},
                  {"from": f"{y}-12-25", "to": f"{y}-12-31", "name": "Winter break (demo dates)"}],
        exam_windows=[{"name": "Quarterly", "from": f"{y}-09-21", "to": f"{y}-09-30"},
                      {"name": "Half-yearly", "from": f"{y}-12-14", "to": f"{y}-12-22"},
                      {"name": "Annual", "from": f"{y + 1}-03-01", "to": f"{y + 1}-03-20"}],
        is_demo=True))
    db.flush()
    rate = {"current": 0.85, "stale": 0.6, "missing": 0.0}
    for cs, prof, end in all_classes:
        cutoff = end or today
        for a in db.scalars(select(TeacherClassAssignment).where(TeacherClassAssignment.class_section_id == cs.id)):
            planning.generate_plan(db, cs, a.subject_id)
            for m in db.scalars(select(TeachingMilestone).where(TeachingMilestone.class_section_id == cs.id,
                                                                 TeachingMilestone.subject_id == a.subject_id)):
                if m.planned_week_start + timedelta(days=6) < cutoff and rng.random() < rate[prof]:
                    planning.mark_taught(db, m, a.teacher_user_id,
                                         at=datetime.combine(m.planned_week_start + timedelta(days=3), time(12)))
    for cs in showcase_classes:
        topic = "Fractions" if cs.grade <= 5 else "Linear equations"
        hits, _ = retrieval.search(db, topic, grade=cs.grade, subject_id=subjects["MATH"].id, k=2)
        teacher = db.scalar(select(TeacherClassAssignment.teacher_user_id).where(
            TeacherClassAssignment.class_section_id == cs.id, TeacherClassAssignment.subject_id == subjects["MATH"].id))
        body = (f"DEMONSTRATION LESSON PLAN (teacher-written example, not AI-generated)\n\n"
                f"LEARNING OBJECTIVE\n- {LO_TEXT[topic][1]}\n\nWARM-UP (5 min)\n- "
                + ("Share a roti or paper circle into equal parts; ask what one part is called." if cs.grade <= 5
                   else "Balance-scale story: 'I added 3 stones and now there are 11. How many were there?'")
                + "\n\nEXPLANATION (15 min)\n- Work through the textbook example on the cited page.\n\n"
                  "GUIDED PRACTICE (10 min)\n- Pairs solve two textbook questions; walk around and listen.\n\n"
                  "CHECK (5 min)\n- Three quick questions on slates; note who needs help.\n\nHOMEWORK\n- One word problem.")
        db.add(LessonPlan(class_section_id=cs.id, subject_id=subjects["MATH"].id, grade=cs.grade, topic=topic,
                          language="en", title=f"{topic} - {cs.label} (demo plan)", body=body,
                          sources=[{**_cite(h), "ref": f"S{i}"} for i, h in enumerate(hits, start=1)],
                          status="approved", ai_generated=False, created_by=teacher, approved_by=teacher,
                          approved_at=utcnow(), is_demo=True))
    for t in TRAINING:
        db.add(TeacherTrainingModule(code=t["code"], title=t["title"], title_hi=t["title_hi"],
                                     subject_id=subjects[t["subject"]].id if t["subject"] else None,
                                     competency_area=t["area"], duration_minutes=t["minutes"],
                                     content={"sections": t["sections"], "check": t["check"]}, is_demo=True))
    db.flush()
    head = db.scalar(select(User.id).where(User.login_id == "demo-t-001"))
    gen = db.scalar(select(TeacherTrainingModule.id).where(TeacherTrainingModule.code == "DEMO-TM-GEN-01"))
    db.add(TrainingCompletion(teacher_user_id=head, module_id=gen, score=1.0))
    db.flush()
