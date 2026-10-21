"""Authenticating front door for an Ollama server running in Colab.

Ollama has no authentication, and a tunnel makes it reachable from the internet, so the tunnel
points here instead. Every request must carry `Authorization: Bearer <GPU_TOKEN>`; only the
read/inference endpoints HAMAR SHIKSHA uses are forwarded (no pull / delete / create / push),
so a leaked token cannot change what is installed on the GPU box. Request bodies are never logged.

The notebook `hamar_gyan_gpu.ipynb` embeds this file; keep the two in step.
Run standalone:  GPU_TOKEN=... UPSTREAM=http://127.0.0.1:11434 uvicorn gpu_proxy:app --port 8080
"""
from __future__ import annotations

import hmac
import os

import httpx
from fastapi import FastAPI, Request, Response

UPSTREAM = os.environ.get("UPSTREAM", "http://127.0.0.1:11434").rstrip("/")
TOKEN = os.environ.get("GPU_TOKEN", "")
ALLOWED = {("POST", "/api/chat"), ("POST", "/api/embed"), ("POST", "/api/embeddings"),
           ("GET", "/api/tags"), ("GET", "/api/version")}
HOP = {"host", "content-length", "connection", "authorization", "accept-encoding", "transfer-encoding"}

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
client = httpx.AsyncClient(base_url=UPSTREAM, timeout=600)


@app.get("/health")
async def health():
    return {"status": "ok"}  # unauthenticated: lets the notebook check the tunnel; reveals nothing


@app.api_route("/{path:path}", methods=["GET", "POST"])
async def forward(path: str, request: Request):
    if not TOKEN:
        return Response("proxy has no GPU_TOKEN configured", status_code=503)
    sent = request.headers.get("authorization", "")
    if not hmac.compare_digest(sent.encode(), f"Bearer {TOKEN}".encode()):
        return Response("unauthorized", status_code=401)
    if (request.method, "/" + path) not in ALLOWED:
        return Response("endpoint not allowed", status_code=403)
    headers = {k: v for k, v in request.headers.items() if k.lower() not in HOP}
    try:
        r = await client.request(request.method, "/" + path, content=await request.body(), headers=headers)
    except httpx.HTTPError:
        return Response("model server unreachable", status_code=502)
    out = {k: v for k, v in r.headers.items() if k.lower() in ("content-type",)}
    return Response(r.content, status_code=r.status_code, headers=out)
