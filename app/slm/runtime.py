"""Gyan SLM inference runtimes.

The platform talks to one small language model through this interface, so
the runtime (Ollama in development / school nodes, a llama.cpp server in
production, or an explicitly configured central service) can be swapped
without touching the education modules.

The model never receives database access or tools: it gets text in and
returns text (or schema-validated JSON) out.
"""
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field

import httpx

from app.config import get_settings


@dataclass
class GenResult:
    text: str
    model: str
    runtime: str
    latency_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    data: dict | None = None
    extra: dict = field(default_factory=dict)

    @property
    def tokens_per_second(self) -> float | None:
        if self.completion_tokens and self.extra.get("eval_ms"):
            return round(self.completion_tokens / (self.extra["eval_ms"] / 1000), 2)
        return None


class SLMUnavailable(Exception):
    """Runtime not configured, unreachable or failed. Callers must fall back
    to a clearly labelled non-AI path."""


def auth_headers(api_key: str | None = None) -> dict[str, str]:
    """Bearer header for a remote runtime behind an authenticating proxy (HS_SLM_API_KEY)."""
    key = api_key if api_key is not None else get_settings().slm_api_key
    return {"Authorization": f"Bearer {key}"} if key else {}


_REASONING = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def strip_reasoning(text: str) -> str:
    """Drop inline <think>...</think> blocks some reasoning models put in the answer. An unclosed
    block (cut off by the token limit) is dropped too: reasoning is never shown or validated."""
    text = _REASONING.sub("", text)
    start = text.lower().find("<think>")
    if start != -1:
        text = text[:start]
    return text.strip()


class OllamaRuntime:
    name = "ollama"

    def __init__(self, url: str, model: str, *, ctx: int, timeout: float, extra_options: dict | None = None,
                 api_key: str | None = None, think: bool | None = None):
        self.url, self.model, self.ctx = url.rstrip("/"), model, ctx
        self.extra_options = extra_options or {}  # e.g. {"num_gpu": 0} to force CPU-only inference
        self.think = think  # None = field not sent (see Settings.slm_think)
        self.client = httpx.Client(timeout=timeout, headers=auth_headers(api_key))

    def generate(self, messages: list[dict], *, max_tokens: int, temperature: float,
                 schema: dict | None = None, model: str | None = None) -> GenResult:
        body = {"model": model or self.model, "messages": messages, "stream": False,
                "options": {"num_predict": max_tokens, "temperature": temperature, "num_ctx": self.ctx,
                            "seed": 7, **self.extra_options}}
        if schema is not None:
            body["format"] = schema
        if self.think is not None:
            body["think"] = self.think
        t0 = time.perf_counter()
        try:
            r = self.client.post(f"{self.url}/api/chat", json=body)
        except httpx.HTTPError as exc:
            raise SLMUnavailable(f"SLM runtime unreachable at {self.url}") from exc
        if r.status_code != 200:
            raise SLMUnavailable(f"SLM runtime error {r.status_code}: {r.text[:200]}")
        d = r.json()
        text = strip_reasoning((d.get("message") or {}).get("content", ""))
        res = GenResult(text=text, model=d.get("model", body["model"]), runtime=self.name,
                        latency_ms=int((time.perf_counter() - t0) * 1000),
                        prompt_tokens=d.get("prompt_eval_count"), completion_tokens=d.get("eval_count"),
                        extra={"eval_ms": (d.get("eval_duration") or 0) / 1e6,
                               "load_ms": (d.get("load_duration") or 0) / 1e6})
        if schema is not None:
            res.data = _parse_json(text)
        return res

    def health(self) -> dict:
        try:
            tags = self.client.get(f"{self.url}/api/tags", timeout=5).json()
            names = [m["name"] for m in tags.get("models", [])]
            return {"ok": True, "runtime": self.name, "model": self.model, "model_available": self.model in names}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "runtime": self.name, "error": str(exc)[:120]}


class LlamaCppRuntime:
    """llama.cpp `llama-server` (OpenAI-compatible /v1/chat/completions)."""

    name = "llamacpp"

    def __init__(self, url: str, model: str, *, timeout: float, api_key: str | None = None):
        self.url, self.model = url.rstrip("/"), model
        self.client = httpx.Client(timeout=timeout, headers=auth_headers(api_key))

    def generate(self, messages, *, max_tokens, temperature, schema=None, model=None) -> GenResult:
        body = {"model": model or self.model, "messages": messages, "max_tokens": max_tokens,
                "temperature": temperature}
        if schema is not None:
            body["response_format"] = {"type": "json_object", "schema": schema}
        t0 = time.perf_counter()
        try:
            r = self.client.post(f"{self.url}/v1/chat/completions", json=body)
        except httpx.HTTPError as exc:
            raise SLMUnavailable(f"SLM runtime unreachable at {self.url}") from exc
        if r.status_code != 200:
            raise SLMUnavailable(f"SLM runtime error {r.status_code}")
        d = r.json()
        text = d["choices"][0]["message"]["content"].strip()
        usage = d.get("usage") or {}
        timings = d.get("timings") or {}
        res = GenResult(text=text, model=body["model"], runtime=self.name,
                        latency_ms=int((time.perf_counter() - t0) * 1000),
                        prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                        extra={"eval_ms": timings.get("predicted_ms", 0)})
        if schema is not None:
            res.data = _parse_json(text)
        return res

    def health(self) -> dict:
        try:
            r = self.client.get(f"{self.url}/health", timeout=5)
            return {"ok": r.status_code == 200, "runtime": self.name, "model": self.model}
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "runtime": self.name, "error": str(exc)[:120]}


class AnthropicRuntime:
    """Optional centralised inference, used only when explicitly configured
    (HS_SLM_RUNTIME=anthropic). Not part of the default offline experience."""

    name = "anthropic"

    def __init__(self, api_key: str, model: str, timeout: float):
        import anthropic

        self._a = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model

    def generate(self, messages, *, max_tokens, temperature, schema=None, model=None) -> GenResult:
        system = "\n\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = [m for m in messages if m["role"] != "system"]
        output_config: dict = {"effort": "low"}
        if schema is not None:
            output_config["format"] = {"type": "json_schema", "schema": schema}
        t0 = time.perf_counter()
        try:
            resp = self.client.messages.create(
                model=model or self.model, max_tokens=max(max_tokens, 1024), system=system, messages=convo,
                extra_body={"output_config": output_config, "fallbacks": "default"},
                extra_headers={"anthropic-beta": "server-side-fallback-2026-07-01"})
        except self._a.APIError as exc:
            raise SLMUnavailable("Central inference service failed") from exc
        if resp.stop_reason == "refusal":
            raise SLMUnavailable("Request declined by central inference service")
        text = "".join(b.text for b in resp.content if getattr(b, "type", "") == "text").strip()
        res = GenResult(text=text, model=resp.model, runtime=self.name,
                        latency_ms=int((time.perf_counter() - t0) * 1000),
                        prompt_tokens=resp.usage.input_tokens, completion_tokens=resp.usage.output_tokens)
        if schema is not None:
            res.data = _parse_json(text)
        return res

    def health(self) -> dict:
        return {"ok": True, "runtime": self.name, "model": self.model, "note": "central cloud inference"}


class NoRuntime:
    name = "none"

    def generate(self, *a, **kw) -> GenResult:
        raise SLMUnavailable("No SLM runtime configured")

    def health(self) -> dict:
        return {"ok": False, "runtime": "none"}


def _parse_json(text: str) -> dict:
    t = text.strip()
    if t.startswith("```"):
        t = t.strip("`")
        t = t[t.find("{"):]
    try:
        return json.loads(t[t.find("{"): t.rfind("}") + 1])
    except (ValueError, json.JSONDecodeError) as exc:
        raise SLMUnavailable("Model returned malformed JSON") from exc


_runtime = None


def get_runtime():
    global _runtime
    if _runtime is None:
        s = get_settings()
        if s.slm_runtime == "ollama":
            _runtime = OllamaRuntime(s.slm_url, s.slm_model, ctx=s.slm_context_tokens, timeout=s.slm_timeout_seconds,
                                     think=s.slm_think)
        elif s.slm_runtime == "llamacpp":
            _runtime = LlamaCppRuntime(s.slm_url, s.slm_model, timeout=s.slm_timeout_seconds)
        elif s.slm_runtime == "anthropic" and s.anthropic_api_key:
            _runtime = AnthropicRuntime(s.anthropic_api_key, s.ai_model, s.ai_timeout_seconds)
        else:
            _runtime = NoRuntime()
    return _runtime


def set_runtime(runtime) -> None:
    """Override (tests / evaluation harness)."""
    global _runtime
    _runtime = runtime
