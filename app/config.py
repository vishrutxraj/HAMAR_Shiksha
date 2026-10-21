"""Runtime configuration, read from environment variables (or a .env file).

Production safety: `Settings.validate_for_production()` refuses to start the
service with development defaults (weak secret, demo data, insecure cookies,
SQLite).
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEV_SECRET = "dev-only-insecure-secret-change-me"


def normalize_database_url(url: str) -> str:
    """Hosted PostgreSQL (Render, Heroku, Neon) hands out postgres:// or postgresql:// URLs, which
    SQLAlchemy would route to the psycopg2 driver. This project ships psycopg 3."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="HS_", extra="ignore")

    environment: str = "development"  # development | test | production
    database_url: str = "sqlite:///./hamar_shiksha.db"
    secret_key: str = DEV_SECRET

    # Sessions
    session_ttl_minutes: int = 8 * 60
    admin_session_ttl_minutes: int = 60
    # Serverless demo without a shared database (each instance has its own copy):
    # a signed ticket cookie lets any instance restore the session. Off by default.
    portable_sessions: bool = False
    cookie_secure: bool = False

    # Brute-force protection
    login_max_attempts: int = 5          # failures per identifier inside the window
    login_window_minutes: int = 15
    lockout_minutes: int = 15
    ip_max_attempts: int = 30            # failures per IP inside the window

    # Admin MFA (TOTP). When true, privileged accounts without MFA cannot log in.
    admin_mfa_required: bool = False

    # Demo data. Must be false in production; demo registry codes are rejected then.
    allow_demo_data: bool = True
    # "full": all 33 districts with classes 1-8 (~3,000 students); "compact": the
    # small footprint used by the automated tests.
    demo_scale: str = "full"

    # Data freshness: a school's latest snapshot older than this is "stale".
    data_current_days: int = 35

    # Retention (days). Applied by `python -m app.cli purge-retention`.
    retention_ai_interactions_days: int = 180
    retention_sessions_days: int = 30
    retention_login_attempts_days: int = 30
    retention_audit_days: int = 365 * 7

    # ---- Gyan SLM (primary AI engine: local small language model) ----
    # Runtime: "ollama" (development / school node), "llamacpp" (llama.cpp
    # server, OpenAI-compatible API), "anthropic" (optional centralised cloud
    # inference - never the default) or "none" (offline templates only).
    slm_runtime: str = "ollama"
    slm_url: str = "http://127.0.0.1:11434"
    # Bearer token for a remote runtime behind an authenticating proxy (e.g. the Colab GPU
    # server in colab/). Sent to generation and embedding calls; empty = no Authorization header.
    slm_api_key: str | None = None
    # Ollama "think" switch for reasoning models (Gemma 4, Qwen 3...). Reasoning tokens would use up
    # slm_max_tokens and leave an empty answer, so a deployment using such a model sets this false.
    # None = do not send the field (models without a thinking mode, e.g. llama3.2).
    slm_think: bool | None = None
    slm_model: str = "llama3.2:3b"
    slm_context_tokens: int = 4096
    slm_max_tokens: int = 700
    slm_temperature: float = 0.2
    slm_timeout_seconds: float = 120.0

    # Curriculum retrieval. Empty embedding model = keyword (BM25) retrieval only.
    kb_dir: str = "data/kb"
    embedding_model: str = ""  # e.g. "BAAI/bge-m3" (multilingual, works offline once cached)
    retrieval_top_k: int = 4
    retrieval_min_vector_score: float = 0.45

    # Voice input: local Whisper speech-to-text ("" = disabled). Audio is
    # transcribed in memory on this server and never stored or sent elsewhere.
    stt_model: str = ""  # e.g. "base" (fast) or "small" (better Hindi)
    stt_max_seconds: int = 30
    stt_requests_per_hour: int = 60

    # Optional centralised cloud inference (explicit opt-in only).
    anthropic_api_key: str | None = None
    ai_model: str = "claude-opus-5-5"
    ai_max_tokens: int = 4000
    ai_timeout_seconds: float = 60.0

    # Module display names (proposed names; configurable until approved).
    module_names: dict = {
        "platform": "NeeevAI Shiksha OS",
        "slm": "Gyan SLM",
        "teacher": "Teacher Saathi",
        "student": "AI Class",
        "assessment": "Pariksha Engine",
        "admin": "Command Centre",
    }

    # Password reset delivery: "log" (development) or an integration name.
    notifier: str = "log"

    @field_validator("slm_think", mode="before")
    @classmethod
    def _empty_think_is_unset(cls, v):
        return None if isinstance(v, str) and not v.strip() else v

    @field_validator("database_url")
    @classmethod
    def _database_url(cls, v: str) -> str:
        return normalize_database_url(v)

    @property
    def is_production(self) -> bool:
        return self.environment == "production"

    def validate_for_production(self) -> list[str]:
        problems = []
        if not self.is_production:
            return problems
        if self.secret_key == DEV_SECRET or len(self.secret_key) < 32:
            problems.append("HS_SECRET_KEY must be set to a random value of at least 32 characters")
        if self.allow_demo_data:
            problems.append("HS_ALLOW_DEMO_DATA must be false in production")
        if not self.cookie_secure:
            problems.append("HS_COOKIE_SECURE must be true in production (HTTPS only)")
        if self.database_url.startswith("sqlite"):
            problems.append("HS_DATABASE_URL must point to PostgreSQL in production")
        if self.notifier == "log":
            problems.append("HS_NOTIFIER must be a real delivery channel in production")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
