"""Remote GPU runtime (Colab server behind an authenticating proxy): the bearer token must reach
both generation and embedding calls, and must be absent when no key is configured."""
from __future__ import annotations

import httpx

from app.config import get_settings
from app.kb import retrieval
from app.slm import runtime as slm_runtime


def test_runtimes_send_bearer_token_only_when_configured():
    rt = slm_runtime.OllamaRuntime("http://gpu.example", "m", ctx=1024, timeout=5, api_key="tok")
    assert rt.client.headers["authorization"] == "Bearer tok"
    rt = slm_runtime.LlamaCppRuntime("http://gpu.example", "m", timeout=5, api_key="tok")
    assert rt.client.headers["authorization"] == "Bearer tok"
    rt = slm_runtime.OllamaRuntime("http://127.0.0.1:11434", "m", ctx=1024, timeout=5)
    assert "authorization" not in rt.client.headers


def test_api_key_comes_from_settings(monkeypatch):
    monkeypatch.setattr(get_settings(), "slm_api_key", "from-env")
    assert slm_runtime.auth_headers() == {"Authorization": "Bearer from-env"}
    assert slm_runtime.auth_headers("explicit") == {"Authorization": "Bearer explicit"}
    monkeypatch.setattr(get_settings(), "slm_api_key", None)
    assert slm_runtime.auth_headers() == {}


def _chat_runtime(monkeypatch, content, **kw):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["body"] = __import__("json").loads(request.content)
        return httpx.Response(200, json={"model": "m", "message": {"role": "assistant", "content": content}})

    rt = slm_runtime.OllamaRuntime("http://gpu.example", "m", ctx=1024, timeout=5, **kw)
    rt.client = httpx.Client(transport=httpx.MockTransport(handler))
    return rt, seen


def test_think_flag_is_sent_only_when_configured(monkeypatch):
    msgs = [{"role": "user", "content": "hi"}]
    rt, seen = _chat_runtime(monkeypatch, "ok")
    rt.generate(msgs, max_tokens=5, temperature=0)
    assert "think" not in seen["body"]
    rt, seen = _chat_runtime(monkeypatch, "ok", think=False)
    rt.generate(msgs, max_tokens=5, temperature=0)
    assert seen["body"]["think"] is False


def test_reasoning_blocks_are_removed_from_answers(monkeypatch):
    assert slm_runtime.strip_reasoning("<think>plan it</think>\nAnswer [S1]") == "Answer [S1]"
    assert slm_runtime.strip_reasoning("Answer <THINK>x\ny</THINK> done") == "Answer  done"
    assert slm_runtime.strip_reasoning("<think>cut off by the token limit") == ""
    rt, _ = _chat_runtime(monkeypatch, "<think>hmm</think>Real answer")
    assert rt.generate([{"role": "user", "content": "q"}], max_tokens=5, temperature=0).text == "Real answer"


def test_embedding_calls_carry_the_token(monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["path"] = request.url.path
        n = len(__import__("json").loads(request.content)["input"])
        return httpx.Response(200, json={"embeddings": [[3.0, 4.0]] * n})

    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client",
                        lambda **kw: real_client(transport=httpx.MockTransport(handler), **kw))
    monkeypatch.setattr(get_settings(), "slm_api_key", "tok")
    monkeypatch.setattr(get_settings(), "slm_url", "http://gpu.example")

    vecs = retrieval._embed_ollama("bge-m3", ["a", "b"])
    assert seen == {"auth": "Bearer tok", "path": "/api/embed"}
    assert vecs.shape == (2, 2)
    assert abs(float((vecs[0] ** 2).sum()) - 1.0) < 1e-6  # normalised
