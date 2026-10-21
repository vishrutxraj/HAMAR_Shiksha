# Test run with the model on a Colab GPU

For a **test run with demo data**. The app (FastAPI, database, textbook search index) runs on your PC or on
Render; the Gyan SLM and the embedding model run on a Colab A100 and are reached through an authenticated
HTTPS tunnel. Nothing in the education modules changed: the app still talks to "Ollama" at `HS_SLM_URL`.

```
Browser -> HAMAR SHIKSHA app (PC / Render) --HTTPS + Bearer token--> Cloudflare quick tunnel
        -> gpu_proxy.py (token check, endpoint allow-list) -> Ollama on Colab A100
                                                               |- gemma4:31b-it-q4_K_M  (answers, lesson plans, ...)
                                                               |- bge-m3                (embeddings for hybrid search)
```

## 1. Start the GPU server

1. Open `colab/hamar_gyan_gpu.ipynb` in Colab (upload it, or from Drive). `Runtime > Change runtime type > A100 GPU`.
2. Optional: add a Colab secret `HS_GPU_TOKEN` so the token survives restarts.
3. Run the cells in order (about 10 minutes the first time, mostly the ~20 GB model download).
4. The last numbered cell prints the `.env` lines. Cell 8 also runs a Hindi chat and an embedding call as a smoke test.

## 2. Point the app at it

```powershell
copy .env.colab.example .env        # then paste the URL / key printed by the notebook
python -m app.cli seed-demo
python scripts/fetch_scert_cg.py ; python -m app.cli ingest-curriculum data/scert_cg/manifest.json   # optional, real books
uvicorn app.main:app --reload
python -m app.cli slm-status        # should show the Colab model as available
```

Ingesting the textbooks embeds about 11,000 passages through the tunnel (batches of 32); expect a few minutes.
Vectors are cached in `HS_KB_DIR`, so only the first ingest pays for it.

On Render instead of a PC: `render.yaml` already carries the same `HS_SLM_*` / `HS_EMBEDDING_MODEL` values;
see `DEPLOY_RENDER.md`.

## 3. Compare the model with the old default (recommended)

The grounding thresholds (`GROUNDING_MIN` 0.45, `SEMANTIC_MIN` 0.55) were calibrated on `llama3.2:3b`.
A larger model writes longer, more fluent answers, so re-measure before trusting them:

```powershell
$env:HS_SLM_URL="https://....trycloudflare.com"; $env:HS_SLM_API_KEY="..."
python -m eval.run_eval --models gemma4:31b-it-q4_K_M --modes base,rag --embedding ollama:bge-m3 --url $env:HS_SLM_URL --tag colab-gemma4-31b
```

Read the saved answers in `eval/results/`, not just the scores; automatic scoring overstates quality
(`docs/MODEL_SELECTION.md`). Gemma 4 has no independent Hindi benchmark yet (`../Edge SLM and cloud LLM stack.docx`).

## About "a larger Chroma DB"

The app does not use Chroma. Retrieval is BM25 plus a flat in-memory vector index built from the passages in the
SQL database (`app/kb/retrieval.py`), with vectors cached as `.npy` files. At 11,000 passages (or hundreds of
thousands) a flat index searches in milliseconds, so there is no scaling reason to add Chroma. The part that needs
a GPU is **computing embeddings**, and that is what moves to Colab here. Retrieval *quality* is governed by passage
size, `HS_RETRIEVAL_TOP_K` and the embedding model, not by the store. Adding Chroma would be a new retrieval
backend; do it only if you specifically want its features.

## Limits and cautions

- **Ephemeral.** The Colab URL (and token, without the secret) change on every restart; sessions idle out and have a
  maximum length. The app falls back to labelled textbook passages when the GPU server is unreachable.
- **Not private.** Questions leave your infrastructure for a Google Colab VM. Demo data only. This contradicts the
  "data stays in the state" design, which still applies to the real deployment.
- **Colab terms.** Exposing a service from a notebook through a tunnel may conflict with Colab's usage policies for
  your plan; check them. This setup is meant for short tests, not hosting.
- **Security.** The tunnel is public but every request needs the bearer token, and only `chat`, `embed`, `tags` and
  `version` are forwarded (no pull/delete/create). Treat the token like a password and clear notebook outputs before sharing.
- **Thinking mode / template quirks** of the Gemma 4 tag on Ollama are untested here. The app sends
  `"think": false` when `HS_SLM_THINK=false` (set in `.env.colab.example` and `render.yaml`) and strips any inline
  `<think>` block from answers. If the Ollama build rejects the field (HTTP 400 mentioning thinking) set
  `HS_SLM_THINK=` (empty) to stop sending it. If the smoke-test answer is still empty or odd, fix that before
  relying on it (the app expects plain answers with `[S1]` citations).
- **HTTP 524 (Cloudflare timeout).** The quick tunnel gives up on any single response slower than about 100 s,
  whatever `HS_SLM_TIMEOUT_SECONDS` says. A cold load of the 31B model takes minutes, so the notebook loads both
  models locally in cell 6b (same `num_ctx` as the app, 8192) before using the tunnel. After a Colab restart,
  run that cell again before using the app. `ollama ps` must show `100% GPU`; a CPU share means the model is too big
  for the GPU you were given (check cell 1) and answers will be slow enough to hit the same limit.
- **Latency** through the tunnel adds round trips; answers from a 31B model take seconds, not milliseconds.
  `HS_SLM_TIMEOUT_SECONDS=300` leaves room for the first request, which loads the model.
- Smaller/faster options: `gemma4:26b-a4b-it-q4_K_M` (mixture of experts) or `gemma4:12b-it-q4_K_M`; on an 80 GB A100,
  `gemma4:31b-it-q8_0`. Change `MODEL` in notebook cell 2 and `HS_SLM_MODEL` in `.env` together.
