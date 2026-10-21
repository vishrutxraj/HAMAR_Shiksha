# HAMAR SHIKSHA application image (FastAPI + static frontend).
# Optional extras: --build-arg WITH_EMBEDDINGS=1 (hybrid retrieval) and
# --build-arg WITH_VOICE=1 (local Whisper speech-to-text; set HS_STT_MODEL=small).
FROM python:3.11-slim AS base

# WEB_CONCURRENCY = uvicorn worker processes (override on small hosts, e.g. 1 on a 512 MB instance)
ENV WEB_CONCURRENCY=2 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    USE_TF=0 TRANSFORMERS_NO_TF=1 HF_HOME=/app/data/hf XDG_CACHE_HOME=/app/data/cache
WORKDIR /app

RUN useradd --create-home --uid 10001 hs
COPY requirements.txt requirements-postgres.txt requirements-embeddings.txt requirements-voice.txt ./
ARG WITH_EMBEDDINGS=0
ARG WITH_VOICE=0
RUN pip install -r requirements-postgres.txt \
 && if [ "$WITH_EMBEDDINGS$WITH_VOICE" != "00" ]; then pip install torch --index-url https://download.pytorch.org/whl/cpu; fi \
 && if [ "$WITH_EMBEDDINGS" = "1" ]; then pip install -r requirements-embeddings.txt; fi \
 && if [ "$WITH_VOICE" = "1" ]; then pip install -r requirements-voice.txt; fi

COPY app ./app
COPY web ./web
COPY migrations ./migrations
COPY alembic.ini ./
COPY scripts ./scripts
COPY data/demo_curriculum ./data/demo_curriculum
# SCERT textbook manifest only; PDFs are fetched (checksum-verified) by scripts/fetch_scert_cg.py
COPY data/scert_cg/manifest.json ./data/scert_cg/manifest.json
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh && mkdir -p /app/data/kb && chown -R hs:hs /app/data
USER hs

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"
ENTRYPOINT ["/entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
