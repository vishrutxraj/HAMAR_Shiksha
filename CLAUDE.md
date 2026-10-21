# HAMAR SHIKSHA

SLM-first, offline-capable school education platform for Chhattisgarh (NeeevAI's EOI response demo).
FastAPI + SQLAlchemy + vanilla JS (no build step). **Read `docs/PROJECT_CONTEXT.md` before making changes**: it holds the
business context, architecture, invariants, extension recipes, known doc inconsistencies and the prioritised gap list.
Business source docs (EOI problem statement, NeeevAI response, edge-SLM research) are one folder up (`..\*.docx`).

Non-negotiables (details in PROJECT_CONTEXT.md §8):
- Authorisation server-side via `app/deps.py` + `app/scope.py`; out-of-scope = 404.
- All figures come from SQL/Python, never from the model; AI output is a draft until a teacher approves; no demo data in production.
- Frontend: vanilla JS, `HS.el`/`textContent` only (no `innerHTML`), strict CSP (no inline script/style, no CDNs), strings in `web/static/js/i18n.js` in en + hi.
- New learner facts go through `services/learner.record_event`; schema changes need an Alembic migration.

Commands (Windows): `python -m pytest` (no GPU/Ollama needed; voice tests need `requirements-voice.txt`),
`python -m app.cli seed-demo`, `uvicorn app.main:app --reload`. Set `PYTHONUTF8=1` when printing Devanagari.

Test-run profile with the model on a Colab GPU: see `docs/DEPLOY_COLAB.md` (`.env.colab.example`, `colab/hamar_gyan_gpu.ipynb`).
Render (app) + Colab (model) test deploy: `render.yaml`, `docs/DEPLOY_RENDER.md`.
