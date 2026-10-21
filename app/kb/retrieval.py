"""Hybrid curriculum retrieval: BM25 (always available, no downloads) plus an
optional multilingual embedding index (FAISS), fused with reciprocal-rank
fusion. Runs fully offline once the embedding model is cached locally.

`search()` returns hits with citation metadata and a `supported` flag that
tells the SLM layer whether there is enough evidence to answer at all.
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
import unicodedata
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import CurriculumChunk, CurriculumDocument, Subject

log = logging.getLogger("hamar_shiksha.kb")
VECTOR_RETRY_SECONDS = 60
TOKEN_RE = re.compile(r"[0-9a-zऀ-ॿ]+")
STOP = set("""a an the is are was were be to of and or in on at for with by from as it this that what which who whom
how why when where do does did can could should would will shall may might i you he she we they my your our their its
me us them about into than then there here also not no yes if so but please tell explain give write define
have has had having many much some any all each every more most other such only own same very just get got
make made find show called mean means meaning example examples kind type types way ways thing things one use
used using need know understand difference between happen happens happened answer question help
है हैं था थे थी का की के को में से पर और या यह वह क्या कैसे क्यों कौन किस किसे एक भी तो ही ने हो होता होती होते
कि जो जब तक बताइए बताओ समझाइए समझाओ लिखिए मुझे हम आप कितने कितना कितनी होते होती कैसा कैसी कहते
कहा जाता जाती अंतर बीच""".split())


def _stem(t: str) -> str:
    """Light English plural folding (ponds->pond, crops->crop, activities->activity).
    Devanagari tokens are left as they are."""
    if not t.isascii() or len(t) <= 3 or not t.isalpha():
        return t
    if t.endswith("ies") and len(t) > 4:
        return t[:-3] + "y"
    if t.endswith(("ches", "shes", "xes", "sses")):
        return t[:-2]
    if t.endswith("s") and not t.endswith(("ss", "us", "is")):
        return t[:-1]
    return t


def tokenize(text: str) -> list[str]:
    text = unicodedata.normalize("NFC", text.lower())
    return [_stem(t) for t in TOKEN_RE.findall(text) if t not in STOP and (len(t) > 1 or t.isdigit())]


@dataclass
class Hit:
    chunk_id: int
    score: float
    bm25: float
    vector: float | None
    matched_terms: int
    text: str
    citation: dict


class _Index:
    def __init__(self):
        self.key = None
        self.ids: list[int] = []
        self.meta: dict[int, dict] = {}
        self.texts: dict[int, str] = {}
        self.tfs: list[Counter] = []
        self.lens: list[int] = []
        self.df: Counter = Counter()
        self.avgdl = 1.0
        self.vectors = None  # numpy array aligned with ids, or None
        self.faiss = None
        self.retry_vectors_at: float | None = None
        self.lock = threading.Lock()

    def _retry_due(self) -> bool:
        return self.retry_vectors_at is not None and time.monotonic() >= self.retry_vectors_at

    # ---------------------------------------------------------------- build --
    def ensure(self, db: Session) -> None:
        key = db.execute(select(func.count(CurriculumChunk.id), func.max(CurriculumChunk.id))).one()
        approved = db.scalar(select(func.count(CurriculumDocument.id)).where(
            CurriculumDocument.approval_status == "approved"))
        key = tuple(key) + (approved, get_settings().embedding_model)  # withdrawal invalidates the index
        if key == self.key and not self._retry_due():
            return
        with self.lock:
            if key == self.key and not self._retry_due():
                return
            rows = db.execute(
                select(CurriculumChunk, CurriculumDocument, Subject)
                .join(CurriculumDocument, CurriculumDocument.id == CurriculumChunk.document_id)
                .outerjoin(Subject, Subject.id == CurriculumChunk.subject_id)
                .where(CurriculumDocument.approval_status == "approved")
                .order_by(CurriculumChunk.id)).all()
            self.ids, self.meta, self.texts, self.tfs, self.lens, self.df = [], {}, {}, [], [], Counter()
            for ch, doc, subj in rows:
                toks = tokenize(f"{ch.chapter or ''} {ch.text}")
                tf = Counter(toks)
                self.ids.append(ch.id)
                self.tfs.append(tf)
                self.lens.append(len(toks))
                self.df.update(tf.keys())
                self.texts[ch.id] = ch.text
                self.meta[ch.id] = {
                    "chunk_id": ch.id, "document_id": doc.id, "document": doc.title, "publisher": doc.publisher,
                    "doc_type": doc.doc_type, "grade": ch.grade, "subject_id": ch.subject_id,
                    "subject": subj.name if subj else None, "chapter": ch.chapter, "page": ch.page,
                    "language": ch.language, "curriculum_version": doc.curriculum_version, "is_demo": doc.is_demo,
                }
            self.avgdl = (sum(self.lens) / len(self.lens)) if self.lens else 1.0
            self.vectors = _load_or_build_vectors(self.ids, self.texts, self.meta)
            # Embedding server unreachable (e.g. the Colab GPU is down): serve keyword results now, but
            # try again after a minute instead of staying keyword-only until the process restarts.
            self.retry_vectors_at = (time.monotonic() + VECTOR_RETRY_SECONDS
                                     if get_settings().embedding_model and self.ids and self.vectors is None else None)
            self.faiss = None
            # numpy search is fast enough for tens of thousands of passages; FAISS is opt-in (HS_USE_FAISS=1)
            if self.vectors is not None and os.environ.get("HS_USE_FAISS") == "1":
                try:  # FAISS inner-product index
                    import faiss

                    self.faiss = faiss.IndexFlatIP(self.vectors.shape[1])
                    self.faiss.add(self.vectors)
                except Exception as exc:  # noqa: BLE001 - missing or broken build (e.g. NumPy ABI) -> numpy search
                    self.faiss = None
                    log.info("FAISS unavailable (%s); using numpy vector search", type(exc).__name__)
            self.key = key
            log.info("KB index built: %d chunks, vectors=%s", len(self.ids), self.vectors is not None)

    # --------------------------------------------------------------- search --
    def idf(self, t: str) -> float:
        n = len(self.ids)
        return math.log(1 + (n - self.df[t] + 0.5) / (self.df[t] + 0.5))

    def bm25(self, q: list[str]) -> list[tuple[float, int, float]]:
        """Per chunk: (score, matched distinct terms, summed IDF of matched terms)."""
        k1, b = 1.5, 0.75
        out = []
        terms = set(q)
        for i, tf in enumerate(self.tfs):
            s, matched, mass = 0.0, 0, 0.0
            for t in terms:
                f = tf.get(t)
                if not f:
                    continue
                idf = self.idf(t)
                matched += 1
                mass += idf
                s += idf * f * (k1 + 1) / (f + k1 * (1 - b + b * self.lens[i] / self.avgdl))
            out.append((s, matched, mass))
        return out


_INDEX = _Index()
_EMBEDDER = None


def _embedder():
    global _EMBEDDER
    name = get_settings().embedding_model
    if not name:
        return None
    if _EMBEDDER is None or _EMBEDDER[0] != name:
        # PyTorch only: never let transformers pull in TensorFlow/Flax on school machines.
        os.environ.setdefault("USE_TF", "0")
        os.environ.setdefault("USE_FLAX", "0")
        os.environ.setdefault("TRANSFORMERS_NO_TF", "1")
        try:
            from sentence_transformers import SentenceTransformer

            _EMBEDDER = (name, SentenceTransformer(name))
        except Exception as exc:  # noqa: BLE001 - model missing offline -> BM25 only
            log.warning("Embedding model %s unavailable (%s); using keyword retrieval only", name, exc)
            _EMBEDDER = (name, None)
    return _EMBEDDER[1]


def _embed_ollama(name: str, texts: list[str]):
    """Embeddings from the local Ollama server (uses its GPU when present), e.g. 'ollama:bge-m3'."""
    import httpx
    import numpy as np

    from app.slm.runtime import auth_headers

    out = []
    try:
        with httpx.Client(timeout=300, headers=auth_headers()) as client:
            for i in range(0, len(texts), 32):
                r = client.post(f"{get_settings().slm_url.rstrip('/')}/api/embed",
                                json={"model": name, "input": texts[i:i + 32]})
                r.raise_for_status()
                out.extend(r.json()["embeddings"])
    except Exception as exc:  # noqa: BLE001 - embedding server down -> keyword retrieval only
        log.warning("Ollama embeddings (%s) unavailable (%s); using keyword retrieval only", name, exc)
        return None
    v = np.asarray(out, dtype="float32")
    return v / np.clip(np.linalg.norm(v, axis=1, keepdims=True), 1e-9, None)


def _embed(texts: list[str]):
    name = get_settings().embedding_model
    if name.startswith("ollama:"):
        return _embed_ollama(name.split(":", 1)[1], texts)
    model = _embedder()
    if model is None:
        return None
    return model.encode(texts, normalize_embeddings=True, batch_size=16, show_progress_bar=False)


def _vec_paths() -> tuple[Path, Path]:
    s = get_settings()
    d = Path(s.kb_dir)
    d.mkdir(parents=True, exist_ok=True)
    tag = re.sub(r"[^a-z0-9]+", "-", s.embedding_model.lower())
    return d / f"vectors-{tag}.npy", d / f"vector-ids-{tag}.npy"


def _load_or_build_vectors(ids, texts, meta):
    if not get_settings().embedding_model or not ids:
        return None
    import numpy as np

    vp, ip = _vec_paths()
    cached: dict[int, object] = {}
    if vp.exists() and ip.exists():
        try:
            for i, v in zip(np.load(ip), np.load(vp)):
                cached[int(i)] = v
        except Exception:  # noqa: BLE001 - corrupt cache -> rebuild
            cached = {}
    missing = [i for i in ids if i not in cached]
    if missing:
        vecs = _embed([f"{meta[i]['chapter'] or ''}\n{texts[i]}" for i in missing])
        if vecs is None:
            return None
        for i, v in zip(missing, vecs):
            cached[i] = v
        np.save(vp, np.array([cached[i] for i in ids], dtype="float32"))
        np.save(ip, np.array(ids, dtype="int64"))
    return np.array([cached[i] for i in ids], dtype="float32")


def search(db: Session, query: str, *, grade: int | None = None, subject_id: int | None = None,
           k: int | None = None) -> tuple[list[Hit], bool]:
    """Return (hits, supported). `supported` is False when the knowledge base
    has no passage with meaningful evidence for the query."""
    s = get_settings()
    k = k or s.retrieval_top_k
    _INDEX.ensure(db)
    if not _INDEX.ids:
        return [], False
    allowed = [i for i, cid in enumerate(_INDEX.ids)
               if (grade is None or _INDEX.meta[cid]["grade"] in (grade, None))
               and (subject_id is None or _INDEX.meta[cid]["subject_id"] in (subject_id, None))]
    if not allowed:
        return [], False
    q = tokenize(query)
    bm = _INDEX.bm25(q)
    lex_rank = sorted((i for i in allowed if bm[i][0] > 0), key=lambda i: -bm[i][0])
    vec_scores = {}
    vec_rank: list[int] = []
    if _INDEX.vectors is not None:
        qv = _embed([query])
        if qv is not None:
            if _INDEX.faiss is not None:
                scores, idx = _INDEX.faiss.search(qv.astype("float32"), len(_INDEX.ids))
                sims = {int(j): float(s) for j, s in zip(idx[0], scores[0])}
                vec_scores = {i: sims[i] for i in allowed}
            else:
                sims = _INDEX.vectors @ qv[0]
                vec_scores = {i: float(sims[i]) for i in allowed}
            vec_rank = sorted(allowed, key=lambda i: -vec_scores[i])[: max(k * 3, 10)]
    fused: Counter = Counter()
    for r, i in enumerate(lex_rank[: max(k * 3, 10)]):
        fused[i] += 1 / (60 + r)
    for r, i in enumerate(vec_rank):
        fused[i] += 1 / (60 + r)
    query_mass = sum(_INDEX.idf(t) for t in set(q)) or 1.0
    hits, lexical_cover = [], {}
    for i, score in fused.most_common(k):
        cid = _INDEX.ids[i]
        lexical_cover[cid] = bm[i][2] / query_mass
        hits.append(Hit(chunk_id=cid, score=round(score, 5), bm25=round(bm[i][0], 3),
                        vector=round(vec_scores[i], 4) if i in vec_scores else None, matched_terms=bm[i][1],
                        text=_INDEX.texts[cid], citation=dict(_INDEX.meta[cid])))

    def evidence(h: Hit) -> bool:
        # Lexical: the matched words must carry at least half of the query's information
        # (IDF-weighted), so one common word cannot make an off-syllabus question look supported.
        lexical = lexical_cover[h.chunk_id] >= 0.5
        semantic = h.vector is not None and h.vector >= s.retrieval_min_vector_score
        return lexical or semantic

    supported = any(evidence(h) for h in hits)
    return hits, supported


def format_citation(c: dict) -> str:
    parts = [c["document"]]
    if c.get("chapter"):
        parts.append(c["chapter"])
    if c.get("page"):
        parts.append(f"p. {c['page']}")
    return ", ".join(parts)
