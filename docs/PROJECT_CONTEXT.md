# HAMAR SHIKSHA: project context for building and updating the system

**Purpose of this file.** A working-memory document for anyone (human or AI assistant) who continues this
project. It records *why* the system exists, *what* is built, *how* it is put together, the rules that must
not be broken, what is still missing, and how to extend it. It complements, and does not replace,
`README.md` and `docs/*.md`. Where those say something different from the code, this file flags it (§12).

- Written: 2026-10-19, from a read of the whole folder, the three business documents next to the repo, and the
  main code paths. §14 lists what was read in full and what was only skimmed.
- Keep it current: when you change behaviour, update the relevant section and the changelog (§15).

---

## 1. One-paragraph summary

HAMAR SHIKSHA ("Holistic Approach for Meaningful Achievements & Reforms in Education"; Chhattisgarhi
*हमर शिक्षा*) is an SLM-first, offline-capable school education platform for government schools in
Chhattisgarh, India. It is the working demo behind **NeeevAI's response to a state Expression of Interest
(EOI)** from the School Education Department. The EOI lists 16 problem statements (planning, tracking,
expertise gaps, AI classes, assessment, analytics, attendance, personalisation, digital infrastructure,
recognition, teacher development, supervision, offline/security). The platform answers all 16 with
**one Gyan SLM + one curriculum knowledge base + one continuous learner record + four modules + three
role portals**. It is FastAPI + SQLAlchemy + vanilla JS, runs locally with Ollama, and is demoed on Vercel.

## 2. Business context (the "why")

| Item | Detail |
|---|---|
| Customer | School Education Department, Government of Chhattisgarh (with SCERT). |
| Vendor | **NeeevAI** (the EOI response is dated 14 Oct 2026, author handle `@Akshat`). The folder sits under `Aigenthix\Top Secret`. NeeevAI already runs an LMS, live expert-led sessions and teacher training for PSEB (Punjab) schools and is piloting a student AI app called *Nova*. |
| Scale (UDISE+ 2026-27, as cited in the EOI) | 38,311 govt schools, 33.16 lakh students, 1,74,156 teachers, PTR 19.04, 5,973 single-teacher schools (~2.13 lakh students). The binding constraint is **subject expertise and continuity**, not headcount. |
| Selection format | Workshop: 15 min presentation + 5 min Q&A per company; focus on practical application, scalability, impact. Skip company/client profiles. |
| EOI's test | Measurable gains in learning outcomes, teacher workload, expertise gaps, assessment, attendance, milestone monitoring, infrastructure use, motivation. "AI should reduce teacher workload, not create another reporting system." |
| Ultimate goal | "No child should be left behind because of lack of teacher availability, subject expertise, timely assessment, personalised support or access to quality digital learning resources." |
| Proposed delivery | 3-month build, 9-month pilot with independent endline, state scale in year 2 only if gains shown. State owns the data and the fine-tuned weights; NeeevAI operates under an SLA. |
| Proposed pilot KPIs | +15 pp grade-level competency; 30 % less admin time; 90 % of uncovered periods covered; 95 % of tests evaluated within 3 days; 80 % of classes on plan; 2x lab utilisation; 70 % weekly active teachers. All marked "[confirm]" in the EOI response. |

**Source documents (in the parent folder `..\`, not in the repo):**

| File | What it is |
|---|---|
| `PROBLEM STATEMENT- Final Ver.docx` | The state's EOI problem statement: 16 expected capabilities, scale, desired outcome. Authoritative for *what the customer asked*. |
| `NeeevAI – EOI Response Chhattisgarh AI in Education.docx` | NeeevAI's response: module names, per-problem solutions, rollout, KPIs, privacy, pitch flow and a pre-submission checklist with open `[placeholders]`. Authoritative for *what we promised*. |
| `Edge SLM and cloud LLM stack.docx` | Research report (Oct 2026) recommending **Gemma 4 E2B** (edge) + **Gemma 4 31B/26B** (cloud teacher), RAG content packs weekly, LoRA adapters per term, base model yearly. Authoritative for *where the AI stack should go*. Not yet reflected in code (§12). |
| `HAMAR_Shiksha\presentation\HAMAR_SHIKSHA_Client_Presentation.pptx` | 27-slide interactive client deck (agenda tiles, live-demo links, roadmap, pilot needs). |

### The 16 problem statements → modules (from the EOI response)

| Cluster | PS | Module |
|---|---|---|
| Plan & track | 1 Academic planning, 2 Milestone tracking/grading | Teacher Saathi, Command Centre |
| Close expertise gap | 3 Expertise gaps, 4 AI support & AI classes, 5 Classroom continuity, 14 Teacher development | Command Centre, Saathi + AI Class |
| Assess & personalise | 7 Assessments, 8 Child profile, 10 Attendance-learning, 11 Personalised remediation | Pariksha Engine, Command Centre, AI Class |
| Lighten & motivate | 6 Minimal workload, 9 Automated analytics, 13 Recognition | all |
| Govern & scale | 12 Digital infrastructure, 15 Admin monitoring, 16 Offline/security/scale | Command Centre, platform |

The repo's `docs/OBJECTIVES.md` maps each of the 16 to screens, code and tests. Use it as the traceability matrix.

## 3. Product at a glance

**Brand names (configurable in `HS_MODULE_NAMES` / `app/config.py`, pending client approval):**
NeeevAI Shiksha OS (platform) · Gyan SLM · Teacher Saathi · AI Class · Pariksha Engine · Command Centre.

| Portal | URL | Users | Main surfaces |
|---|---|---|---|
| Admin | `/admin` | state / district / block admins | Command Centre: overview, progress grades, expertise gaps, attendance follow-up, digital infrastructure, recognition, map & divisions, schools, registry, onboarding (maker-checker), reports/exports, audit, curriculum & AI, devices |
| Teacher | `/teacher` | verified teachers, head teachers | Saathi (academic plan, lesson plans, worksheets, remediation, micro-courses, substitute lesson, Ask), Pariksha (question bank, assessments, review queue), class progress, priorities, student help, progress & follow-up, digital resources, recognition |
| Student | `/student` | students (Hindi by default) | AI Class: home (level, streak, weekly challenge), Ask Gyan (typed or voice), lessons, quizzes (offline-capable), Fix my mistakes, textbook library, goals, doubts, study buddies, badges, My progress |

Extras: voice input (local Whisper), 37 SCERT Chhattisgarh textbooks ingestable (Kruti Dev to Unicode),
33-district / 5-division map, light/dark theme + colour schemes, Hindi/English UI.

## 4. Architecture

```
Browser (vanilla JS, service worker, IndexedDB outbox)
   | HTTPS, httpOnly session cookie + CSRF header
FastAPI app (app/main.py)  -- security headers, strict CSP, gzip, portal page gates
   routers/ ----> deps.py (Principal, RBAC)  ----> scope.py (data scope, 404 on out-of-scope)
   services/ (deterministic rules: learner, planning, monitoring, analytics, pariksha, ...)
   slm/gyan.py (grounded generation)  --> kb/retrieval.py (BM25 + optional bge-m3 vectors)
                                      --> slm/runtime.py (Ollama | llama.cpp | anthropic opt-in | none)
SQLAlchemy models (55 tables) + Alembic (3 migrations); SQLite dev/demo, PostgreSQL production
```

Key design decisions (do not reverse without a recorded reason):

| Decision | Choice | Why |
|---|---|---|
| Primary AI | Local open-weight 1.5-3B SLM behind a runtime adapter | Offline, no per-use cost, child data stays in-state |
| Grounding | RAG + deterministic citation/grounding validation | Small models hallucinate; "verified" must mean something |
| Official numbers | SQL/Python only, never the model | Reproducible, auditable |
| Frontend | Vanilla JS, **no build step**, no `innerHTML` | ~20 KB pages for 2G/3G phones, runs on a school PC without Node, strict CSP |
| Auth | Server-side sessions, httpOnly cookie, CSRF header | Revocable; nothing in JS-readable storage |
| Learner record | Append-only `learner_events` (unique `event_uid`) + derived tables | One fact reused by all modules; idempotent offline sync |
| Cloud LLM | Optional, off by default (`HS_SLM_RUNTIME=anthropic`) | Spec: not the default for student interactions |
| Demo safety | Everything `is_demo`; production refuses demo data | Never confuse demo with official data |

### 4.1 The Gyan SLM answer pipeline (`app/slm/gyan.py`)

1. Injection regex screen. Hit gives a safe fixed reply.
2. `retrieval.search` (class- and subject-filtered). No sufficient evidence gives "could not verify".
3. Passages go to the model as `<source id="S1" ...>` data blocks. The system prompt: use only sources, cite `[S#]`, otherwise reply `NOT_FOUND`.
4. Post-process: strip LaTeX (`plain_maths`), normalise citation markers, drop markers to non-existent passages, scrub phone/email (`[removed]`).
5. Validate: word coverage against passages `>= GROUNDING_MIN = 0.45`. If the answer and the passages are in different scripts, accept on embedding similarity `>= SEMANTIC_MIN = 0.55`.
6. Verified answer + book/chapter/page citations, or the honest "could not verify" message.
7. If the runtime is down, return the best textbook passage labelled as **not AI-generated** (`ai_generated=False`, `reason="slm_unavailable"`).

Thresholds were calibrated on the eval run (correct median 0.88, hallucination 0.38). Changing them needs a re-run of `eval/run_eval.py`. The model gets no tools, no DB access, no personal data.

### 4.2 Learner record and analytics

- Every attendance mark, assessment result, activity completion, lesson read, revision review, peer-help action writes **one idempotent `LearnerEvent`** (`services/learner.record_event`). A reused `event_uid` for a different student is rejected with 409 (forgery guard).
- `assessment_result` / `activity_completion` events update `LearnerCompetency` per learning outcome. Items flagged `needs_review` are excluded until a teacher finalises them. Status bands: achieved >= 0.75, needs practice < 0.6, min 3 attempts (`learner.py`).
- Dashboards read aggregates (`school_performance_snapshots`, nightly `recompute-snapshots`), never chat text. Outcome metrics use only schools whose data is *current* (`HS_DATA_CURRENT_DAYS`, 35) and say how many were current/stale/missing.
- Rules engines: `insights.py` ("rules-engine-v1", every recommendation stores its evidence rows), `monitoring.py` (grades A-D, attendance follow-up), `planning.py` (calendar to week-wise milestones, planned/taught/learned), `recognition.py` (badges computed on read, nothing stored to drift).

### 4.3 Offline and sync (`services/sync.py`, `web/static/js/sw.js`)

- Service worker (`hs-shell-v4`): network-first cache of static assets and portal shells; **API responses are never cached** by it. Offline data lives in the signed-in user's IndexedDB bundle (quizzes, rosters) and is wiped on logout.
- Queue: `attendance.mark`, `milestone.taught` (teacher); `assessment.submit`, `activity.complete` (student). Max 200 events per push; every event re-validated by the same service functions as online, under the user's own permissions; device must be registered, un-revoked and in the same school.
- Conflict rules: attendance latest client timestamp wins (editable window 7 days); assessment first submission wins; activity best score kept; milestone idempotent. Every outcome stored in `synchronization_events`.
- Not built: school-node to state-server replication; on-device (Android) SLM.

### 4.4 Security model (details in `docs/SECURITY_PRIVACY.md`)

- Three portals; an account works only on its own. `deps.require_portal` / `require_admin`. Teachers must be `verified` at an `active` school.
- Scope comes **only** from active `user_role_assignments`. Out-of-scope objects return **404**, never 403 (no existence oracle). `scope.py` is the single place for scope rules: reuse it, do not hand-roll `where` clauses.
- Students: APIs take no student id (identity from the session). Teachers address students by random `public_id`, only inside assigned classes.
- Argon2id passwords; sessions stored as SHA-256 digests; 60-min admin TTL; lockout 5 failures/15 min; optional TOTP MFA (`HS_ADMIN_MFA_REQUIRED`); CSRF header on non-GET.
- Admins get **counts, never names**, for attendance follow-up; groups under 5 suppressed.
- Audit log for logins, denials, record views, exports, AI usage, reviews, sync, device revocation (`app/audit.py`).
- Production guard (`Settings.validate_for_production`): refuses default/short secret, demo data, insecure cookies, SQLite, `notifier=log`.
- CSP is `default-src 'self'` (no inline script/style, no CDNs/fonts). Any new asset must be served from `/static`.

## 5. Repository map (`HAMAR_Shiksha/`)

| Path | Contents |
|---|---|
| `app/main.py` | App factory, headers/CSP, router registration, page routes with server-side portal gate |
| `app/config.py` | `Settings` (`HS_*` env vars), module names, production validation |
| `app/models.py` | 55 SQLAlchemy models (~1,000 lines); constants `PORTALS`, `ADMIN_ROLES`, `ROLE_PORTAL` |
| `app/deps.py`, `scope.py`, `security.py`, `audit.py` | Principal/RBAC/CSRF, scope helpers, hashing/tokens/TOTP, audit writer |
| `app/routers/` | 172 route handlers: `admin` (46, the largest), `teacher` (30), `growth` (23), `saathi` (18), `student` (16), `engage` (11), `auth` (9), `pariksha` (8), `kb` (6), `sync` (3), `voice` (2) |
| `app/services/` | Business logic (see §4.2). `studenthelp`, `digital`, `monitoring`, `recognition`, `planning`, `pariksha`, `analytics`, `insights`, `learner`, `sync`, `aiclass`, `content`, `accounts`, `exports`, `stt`, `ocr` (stub) |
| `app/slm/` | `gyan.py` (grounded generation, lesson/question/remediation drafts), `runtime.py` (Ollama / llama.cpp / anthropic / none) |
| `app/kb/` | `ingest.py` (PDF/TXT to ~120-word passages with page/chapter), `retrieval.py` (BM25 + FAISS/bge-m3, RRF), `legacy_fonts.py` (Kruti Dev to Unicode) |
| `app/registry.py`, `geo.py` | School-code registry (UDISE+ 11-digit rules, demo range 2260-2299), 5 divisions / 33 districts |
| `app/demo_seed.py`, `demo_content.py` | Deterministic synthetic dataset (about 3,600 students, 170 teachers at `full` scale) |
| `app/cli.py` | `init-db, seed-demo, create-state-admin, import-registry, recompute-snapshots, refresh-insights, purge-retention, purge-demo, ingest-curriculum, slm-status, schema-sql` |
| `app/serverless.py`, `api/index.py`, `vercel.json` | Vercel entry, `/tmp` demo DB from `deploy/demo.db.gz`, signed "portable session" ticket cookie |
| `migrations/versions/` | `0001_initial_schema`, `0002_divisions_and_student_self_improvement`, `0003_digital_infrastructure_and_recognition` |
| `web/pages/*.html` | 8 thin shells (index, reset, 3 logins, 3 dashboards) |
| `web/static/js/` | `core.js` (helpers, i18n, `HS.el`, api), `admin.js` (71 KB), `teacher.js` (83 KB), `student.js` (49 KB), `charts.js`, `i18n.js`, `voice.js`, `theme.js`, `login.js`, `sw.js` |
| `web/static/` | `css/app.css`, `data/cg-districts.geojson`, `manifest.webmanifest` |
| `data/demo_curriculum/` | Demo primers (NOT SCERT) + manifest. `data/scert_cg/manifest.json` lists 37 official textbook URLs + SHA-256 (PDFs are fetched, not committed) |
| `eval/` | `run_eval.py`, `gyan_eval.jsonl` (38-39 items), `results/` (Oct 14 runs), `sample_workflow.py` |
| `scripts/` | SCERT download/fetch/add scripts, `build_vercel_demo_db.py` |
| `tests/` | 12 files, 129 default tests + 3 opt-in live-SLM tests (`-m slm`) |
| `docs/` | ARCHITECTURE, OBJECTIVES, MODEL_SELECTION, CURRICULUM, TECHNICAL, DEPLOY_VERCEL, SECURITY_PRIVACY, LIMITATIONS, API (+ `openapi.json`), ADMIN/TEACHER guides, DEMO_ACCOUNTS, SAMPLE_WORKFLOW |
| `Dockerfile`, `docker-compose.yml`, `docker/` | App + PostgreSQL 16 + Ollama + Caddy (compose validated, image build **not** executed) |

Stack: Python 3.12 (pinned), FastAPI >=0.111, SQLAlchemy 2, Alembic, pydantic-settings, argon2-cffi, openpyxl, pdfplumber, httpx, psycopg 3. Optional: sentence-transformers/FAISS/torch (`requirements-embeddings.txt`), `openai-whisper` + numpy (`requirements-voice.txt`), `anthropic` (opt-in).

## 6. Domain model cheat-sheet

- **Geography/org:** `Division` > `District` > `Block` > `School` (+ `SchoolCodeRegistry`, the only source of valid codes), `ClassSection`, `Subject`.
- **People:** `User`, `UserRoleAssignment` (role + district/block/school scope), `TeacherProfile` (`verification_status`, `subject_qualifications`), `StudentProfile` (consent recorded), `TeacherClassAssignment`, `StudentEnrollment`, `TimetableSlot`.
- **Curriculum:** `CurriculumDocument` > `CurriculumChunk` (class, subject, chapter, page, language, version, `is_demo`), `LearningOutcome` (codes like `DEMO-MATH5.2`), `AcademicCalendar`.
- **Teaching:** `LessonPlan` (draft until approved), `TeachingMilestone` (planned week, taught_at), `ClassSession` (substitute lessons), `RemediationPlan`, `TeacherTrainingModule`/`TrainingCompletion`.
- **Assessment:** `Question`, `AnswerKey` (never sent to offline bundles), `Assessment`, `AssessmentResult`, `LearnerCompetency`.
- **Learner record:** `LearnerEvent`, `LearningProgress`, `AttendanceRecord`, `AIRecommendation`, `AIInteraction` (retained 180 d), `StudentGoal`, `RevisionItem`, `Doubt`/`DoubtReply`, `StudyPair`, `Appreciation`.
- **Infra/ops:** `DeviceRegistry`, `SynchronizationEvent`, `DigitalResource`/`DigitalUsage`, `SchoolPerformanceSnapshot`, `SchoolReport`, `AuthSession`, `LoginAttempt`, `PasswordResetToken`, `AuditLog`.

## 7. Running, testing, deploying

```bash
# from HAMAR_Shiksha/
python -m venv .venv && .venv\Scripts\activate
pip install -r requirements.txt              # add -r requirements-embeddings.txt -r requirements-voice.txt for full features
ollama pull llama3.2:3b ; ollama pull bge-m3
copy .env.example .env
python -m app.cli seed-demo                  # runs migrations + demo data (HS_DEMO_SCALE=compact for tests/fast)
python scripts/fetch_scert_cg.py && python -m app.cli ingest-curriculum data/scert_cg/manifest.json   # optional, ~1.3 GB
uvicorn app.main:app --reload                # http://127.0.0.1:8000, API docs at /api/docs (non-production)
python -m pytest                             # no GPU/Ollama needed (fake SLM + keyword retrieval)
HS_LIVE_SLM_MODEL=llama3.2:3b python -m pytest -m slm
python -m eval.run_eval --models llama3.2:3b --modes base,rag --embedding BAAI/bge-m3
docker compose --env-file .env.docker up -d --build
```

Demo logins (all fictional): `state.admin / Demo@State2026`; `district.<name> / Demo@District2026`;
`block.dharsiwa / Demo@Block2026`; teachers `DEMO-T-001` (head), `DEMO-T-002` (maths/science) `/ Demo@Teacher2026`;
students `st-demo01` (Class 5), `st-demo02` (Class 8) `/ Demo@Student26`. Per-district list in `docs/DEMO_ACCOUNTS.md`.
These are public demo credentials in the docs and deck, so never reuse the pattern for anything real.

**Test-run profile (added 2026-10-21): model on a Colab GPU.** `colab/hamar_gyan_gpu.ipynb` starts Ollama (Gemma 4 31B 4-bit + bge-m3) on a Colab A100 behind `colab/gpu_proxy.py` (bearer token, endpoint allow-list) and a Cloudflare quick tunnel; the app uses it via `HS_SLM_URL` + new `HS_SLM_API_KEY` (sent by `OllamaRuntime`, `LlamaCppRuntime` and the Ollama embedding call). Template: `.env.colab.example`; guide: `docs/DEPLOY_COLAB.md`. URL/token change on each notebook restart. Demo data only. The app has **no Chroma**; retrieval is BM25 + a flat numpy/FAISS index (see the guide).

Environments: **Vercel** = demo only (bundled SQLite copy per instance, portable session cookie, no SLM/vectors/voice unless
`HS_SLM_URL` points to your own Ollama; do not set `HS_ENVIRONMENT=production` there). **Docker/VM** = the real
deployment shape. Demo DB ages: after ~35 days schools show as "stale"; rebuild with
`python scripts/build_vercel_demo_db.py`.

Scheduled jobs: nightly `recompute-snapshots --months 2` then `refresh-insights`; weekly `purge-retention`.

## 8. Conventions and invariants (what a change must respect)

1. **Authorisation is server-side and scope-based.** New endpoint = `Depends(require_*)` + use `app/scope.py` helpers; unknown/out-of-scope object = `scope.not_found()` (404). Add a case to `test_authorization.py`.
2. **No fabricated numbers.** Any figure shown to users must be computed from records (SQL/Python). The model never produces statistics. Production must not show demo data (`test_production_guards.py`).
3. **AI output is a draft until a human approves it** (lesson plans, worksheets, questions, remediation). The UI must distinguish AI-generated from approved/textbook text; the offline fallback is labelled "from your textbook", not AI.
4. **Uncertain scoring goes to a teacher.** Short-answer near-misses (similarity 0.5-0.92) and all descriptive/handwritten answers enter the review queue; only final scores update competencies.
5. **Write once, reuse.** New learner facts go through `learner.record_event` with a stable `event_uid`; do not add parallel tables for the same fact. Offline sync must call the *same* service function as the online path.
6. **Privacy wording and rules.** Never label children ("weak", "dropout"); use "follow up"/"keep watch". Admin views are aggregate-only for children; small groups (<5) suppressed. No third-party scripts/fonts/trackers.
7. **Frontend:** vanilla JS only; build DOM with `HS.el` and `textContent` (no `innerHTML`); no inline `<script>`/`style` (CSP); add both English and Hindi strings to `i18n.js`; keep pages light for 2G/3G; if you add a new static file that must work offline, add it to `SHELL` in `sw.js` and bump `CACHE`.
8. **Schema changes need an Alembic migration** (`alembic revision --autogenerate`), reviewed by hand, plus the demo seed/`deploy/demo.db.gz` rebuilt if the demo depends on it.
9. **Time:** timestamps are naive UTC (`models.utcnow`); client ISO timestamps are normalised in `sync._parse_ts`.
10. **Honesty in docs.** `LIMITATIONS.md` is deliberate and valued by the client-facing narrative; add to it whenever a shortcut is taken. Do not write "tested" for things only verified manually.
11. **Safety screens are pattern-based** (phone/links/unkind words/wellbeing phrases in English + Hindi). Extend patterns, but do not claim coverage of Chhattisgarhi or transliterated Hindi.
12. **Windows host:** the developer machine is Windows 11; write scripts/commands that work in PowerShell, and open text files as UTF-8 (Devanagari content breaks cp1252 defaults).

## 9. How to extend (recipes)

**Add an API feature end to end**
1. Model in `app/models.py` + migration in `migrations/versions/` (next number `0004_...`).
2. Logic in `app/services/<area>.py` (pure functions taking `db` and ids; no request objects), so sync/CLI can reuse it.
3. Route in the matching `app/routers/*.py` using the right `require_*`; audit sensitive reads/writes with `audit(...)`.
4. UI: add a tab/section in `admin.js`, `teacher.js` or `student.js`; strings in `i18n.js` (en + hi); styles in `app.css` (keep both themes working).
5. Tests in the nearest `tests/test_*.py` (see `conftest.py` for the client/login helpers and the fake SLM); add the objective to `docs/OBJECTIVES.md` if it maps to one.
6. Regenerate `docs/openapi.json`/`docs/API.md` (API.md is "generated from OpenAPI"; the generator is not in the repo, so use `/api/openapi.json` from a dev server).

**Change the AI model / runtime.** Set `HS_SLM_RUNTIME`, `HS_SLM_URL`, `HS_SLM_MODEL`. To add a runtime, implement the `generate(messages, max_tokens, temperature) -> GenResult` contract in `slm/runtime.py`. Re-run the eval (`eval/run_eval.py`) and re-calibrate `GROUNDING_MIN` if the model's style differs.

**Add a textbook / language.** Add to a manifest (`file`, `title`, `doc_type`, `subject`, `grade`, `language`, `curriculum_version`, `publisher`, optional `legacy_font: "krutidev"`), run `ingest-curriculum`. Enabling a new answer language needs `gyan.SUPPORTED_LANGUAGES`, the `UNVERIFIED`/`INJECTION_REPLY` strings, UI strings, eval items validated by native speakers, and a documented accuracy statement.

**Add a registry/real data feed.** UDISE+ goes through `registry.import_registry_csv` (read-only). HRMS/attendance connectors do not exist; `TeacherProfile.subject_qualifications` is the field the expertise-gap logic reads.

## 10. Current state

**Done and evidenced:** all 16 objectives have a feature, a screen and at least one test (`docs/OBJECTIVES.md`); 20 acceptance criteria mapped to tests (README); local SLM benchmark on 5 candidate models; hybrid retrieval; voice input; Kruti Dev conversion; 37 SCERT textbooks ingested (11,057 passages); 33-district demo with ~3,600 students; Vercel demo; Docker compose; 27-slide client deck.

**Benchmark result that drives the current default (demo primers, 38 items, one laptop with RTX 3050 4 GB):** `llama3.2:3b` + RAG + bge-m3 scores 1.000 automatic (about 0.83 Hindi after manual review), p50 2.7 s. `gemma2:2b` 0.944 and ~10 s on CPU; `qwen2.5:1.5b` 0.917. Without retrieval every model answered every off-syllabus question (refusal 0.0): *architecture, not the model, provides honesty*.

**Test baseline:** 127 passed / 2 failed (missing optional voice deps only); details in §14.

## 11. Gaps and roadmap (prioritised)

Items where the **EOI response promised something the code does not yet do**, or where a customer would probe:

| Priority | Gap | Notes |
|---|---|---|
| High | **Model/licence decision.** Default is Llama 3.2 3B (Llama Community Licence: "Llama" naming/attribution, awkward for a state-branded "Gyan SLM"); alternates carry Gemma ToU or Qwen Research Licence. The research doc recommends **Gemma 4 E2B** (Apache-2.0) edge + Gemma 4 31B teacher | Next step in the research: 2-week benchmark on the actual smart-class PCs/phones; build Hindi + Chhattisgarhi eval sets rated by native speakers; PoC of LoRA hot-swap (llama.cpp vs LiteRT-LM). Re-run `eval/run_eval.py` with Gemma 4 tags once available in Ollama |
| High | **Answer correctness.** 3 of 4 spot-checked answers on real SCERT books were wrong/vague though "verified" (CURRICULUM.md §2) | Teacher review panels, SCERT-scored eval set, stronger central model for hard queries, fine-tune later. Pilot only with teacher supervision. Benchmark not re-run on real books |
| High | **No fine-tuning exists**, yet the EOI says "fine-tuned on SCERT textbooks/outcomes/papers" | Align messaging or build the QLoRA pipeline (MODEL_SELECTION §6; research doc: per-term adapters, on-policy distillation, replay, KL drift gates). Needs approved, de-identified data |
| High | Learning outcomes and question banks are the **demo set**, not mapped to the 37 real books | Needs SCERT-aligned outcome codes and reviewed items |
| Medium | **Chhattisgarhi, Gondi, Halbi** are promised; none enabled. No benchmark or data exists for any model | Hindi/English only today. Gondi/Halbi realistically need a glossary/translation layer with human post-editing, not open tutoring |
| Medium | **Handwritten/OMR/camera scanning** of answer sheets (EOI PS7) | `services/ocr.py` is an interface stub; all handwritten answers go to review |
| Medium | **Auto "substitute period" when absence is marked**, video explainers, weekly 5-question checks | Today: head teacher launches an approved plan manually; no video |
| Medium | **Integrations:** UDISE+ API, HRMS, state attendance/assessment portals, SSO (e-Pramaan), SMS/WhatsApp/IVR gateways | Only CSV registry import exists; notifier is a stub (production refuses `log`) |
| Medium | **Device telemetry** for ICT labs/smart classes | Digital usage is teacher-recorded, not telemetry |
| Medium | State-level replication of school nodes; on-device Android SLM | Not built |
| Medium | Real hardware benchmarks (8 GB PCs, mid-range Android); load test for ~56,000 schools (EOI says 38,311, see §12) | Only one dev laptop measured |
| Low | Frontend tests (none; UI verified by API journeys + manual) | Consider Playwright smoke tests |
| Low | Textbooks not loaded: 7 (scanned = needs OCR; Shree-Lipi font; one download failure) | See CURRICULUM.md |
| Low | WCAG audit, VAPT (CERT-In), DPIA, SCERT permission letter | Pre-go-live items |

**Commercial/process items open in the EOI response** (not engineering): confirm product names, base model, device specs and offline demo build, cost per student, PSEB/Nova evidence, sample Chhattisgarhi chapter pack, language partners, department data-system access, turn the EOI doc into the 15-min deck. Suggested pitch flow: problem (1 min), one system (2), teacher's week demo (4), child's week demo (3), officer view (2), **offline demo with Wi-Fi off** (1), rollout/KPIs/cost (2).

## 12. Inconsistencies and traps found while reading

Fix or be aware of these before trusting a document:

1. **Table count.** `ARCHITECTURE.md`/`TECHNICAL.md`/README say **46 tables**; `models.py` defines **55** (migrations 0002/0003 added divisions, goals, revision items, doubts, study pairs, digital resources, appreciations).
2. **Test count.** `TECHNICAL.md` says 103 tests; the suite actually collects 129 default tests (119 test functions, some parametrised) plus 3 opt-in live-SLM tests (§14).
3. **Embedding setting.** `config.py` default is empty (BM25 only); `.env.example` uses `ollama:bge-m3`; `TECHNICAL.md` and `MODEL_SELECTION.md` say `BAAI/bge-m3`. The benchmark used `BAAI/bge-m3` via sentence-transformers, production-style runs use `ollama:bge-m3`; semantic thresholds (0.55, 0.45) were calibrated on the former, so re-check them if you switch.
4. **School count.** The EOI/problem statement says 38,311 schools; `LIMITATIONS.md` mentions capacity planning for ~56,000 schools. Use 38,311 (UDISE+ 2026-27 as cited by the state) unless told otherwise.
5. **Model docs vs research.** Code and `MODEL_SELECTION.md` say Llama/Gemma 2/Qwen 2.5; the research doc says Gemma 4 and calls the EOI's 1-4B range sensible. The code default `ai_model` for the opt-in cloud path is `claude-opus-5-5` (config.py), unrelated to the local SLM.
6. **Eval dataset size** is quoted as 38 and 39 items in different places.
7. **`docs/DEPLOY_VERCEL.md`** says ~1,000 students / 57 schools in the bundled DB; README says ~3,600 students (that is the `full` seed, not the bundled DB).
8. **District boundaries:** the map shows 28 shapes for 33 districts (five 2022 districts drawn inside their parents); figures for those five appear only in the table.
9. **Demo credentials** are printed in docs, the deck (slide 23) and the Vercel demo: acceptable for a demo, a leak if copied anywhere real.
10. **Windows console:** printing Devanagari from Python on a default cp1252 console raises `UnicodeEncodeError`; set `PYTHONIOENCODING=utf-8` or `PYTHONUTF8=1`.
11. `tests/__pycache__` and `.gitattributes` exist but the folder is **not a git repository** locally; the remote is `Niladri962/HAMAR_Shiksha` on GitHub per `DEPLOY_VERCEL.md`. Check with the owner before assuming history exists.

## 13. Glossary

SLM small language model · RAG retrieval-augmented generation · BM25 keyword ranking · bge-m3 multilingual embedding model ·
RRF reciprocal rank fusion · SCERT State Council of Educational Research and Training (Chhattisgarh) · UDISE+ national school data system ·
HRMS HR management system · PTR pupil-teacher ratio · PS problem statement (1-16) · LO learning outcome · Kruti Dev / DevLys legacy Devanagari fonts ·
Samagra Shiksha centrally sponsored school scheme · DPDP Act 2023 India's data-protection law · CHILDLINE 1098 child helpline surfaced to children in distress ·
LoRA/QLoRA low-rank adapter fine-tuning · ICT@School national ICT scheme · Maker-checker onboarding needing a second admin to activate.

## 14. Verification status of this document

- **Read in full:** README, ARCHITECTURE, OBJECTIVES, LIMITATIONS, TECHNICAL, MODEL_SELECTION, SECURITY_PRIVACY, DEPLOY_VERCEL, CURRICULUM, the three `.docx` files, `config.py`, `main.py`, `deps.py`, `scope.py`, `serverless.py`, the answer pipeline in `slm/gyan.py` (first 210 lines), `services/learner.py` and `services/sync.py` (openings), `.env.example` (first 60 lines), `requirements.txt`, `vercel.json`, `pytest.ini`.
- **Skimmed only (docstrings, signatures, grep counts):** all other `services/`, `kb/`, `routers/` bodies, `models.py` column definitions, `cli.py`, the front-end JS (only `core.js` head and `sw.js`), migrations, `demo_seed.py`, `eval/`, `API.md`, `openapi.json`, `SAMPLE_WORKFLOW.md`, `ADMIN_GUIDE.md`, `TEACHER_GUIDE.md`, the CSS, the PPTX (slide text only).
- **Not verified by running:** the app, the SLM, Docker. Statements about benchmark numbers and limits come from the repo docs.
- **Test baseline (run 2026-10-19 in a throw-away venv, Python 3.12.6, no Ollama):** **127 passed, 2 failed, 3 deselected** (61 s). The 2 failures (`test_voice_validates_audio_and_never_invents_text_from_silence`, `test_wav_decoding_resamples_to_16k`) are `ModuleNotFoundError: numpy`: the voice tests need `requirements-voice.txt`, which this scratch venv did not install. So the app itself is healthy; install the voice extras (or make those two tests skip when numpy is absent) for a fully green run. The suite needs no Ollama or GPU.

When picking up work in a skimmed area, read that code first and correct this file if it is wrong.

## 15. Changelog of this document

- 2026-10-21: added the Colab GPU test-run profile (`HS_SLM_API_KEY`, `colab/`, `.env.colab.example`, `docs/DEPLOY_COLAB.md`, `tests/test_remote_runtime.py`). Suite is now 132 passing with voice extras installed (the earlier 2 failures were only missing `numpy`). The notebook itself has **not** been run on Colab yet; the proxy was tested locally against a fake Ollama.

- 2026-10-19: created from first full exploration.
- 2026-10-21 (later): Render readiness. `render.yaml` Blueprint (Docker web service + Postgres, `HS_ENVIRONMENT=demo`, `PORT=8000`, `WEB_CONCURRENCY=1`) and `docs/DEPLOY_RENDER.md`. Code: `normalize_database_url` (`postgres(ql)://` -> `postgresql+psycopg://`) in `app/config.py`; `HS_SLM_THINK` (sent to Ollama as `think`, empty = unset) and `strip_reasoning` for `<think>` blocks in `app/slm/runtime.py`; retrieval retries embeddings every 60 s after a failed attempt instead of staying keyword-only until restart; `numpy` added to `requirements.txt` (the Docker image had none, so `ollama:bge-m3` would have raised ImportError); Dockerfile worker count now from `WEB_CONCURRENCY`. Suite: 143 passing. Not verified: the Docker image build (Docker Desktop was not running) and an actual Render deploy.
