"""Hosted-deployment glue: provider database URLs, and keyword-only retrieval recovering when the
embedding server (e.g. the Colab GPU) comes back."""
from __future__ import annotations

import pytest

from app.config import Settings, normalize_database_url
from app.kb import retrieval


@pytest.mark.parametrize("given, expected", [
    ("postgresql://u:p@dpg-x-a/db", "postgresql+psycopg://u:p@dpg-x-a/db"),
    ("postgres://u:p@host:5432/db?sslmode=require", "postgresql+psycopg://u:p@host:5432/db?sslmode=require"),
    ("postgresql+psycopg://u:p@host/db", "postgresql+psycopg://u:p@host/db"),
    ("sqlite:///./x.db", "sqlite:///./x.db"),
])
def test_database_url_is_normalised_for_psycopg3(given, expected):
    assert normalize_database_url(given) == expected
    assert Settings(database_url=given).database_url == expected


def test_database_url_from_environment(monkeypatch):
    monkeypatch.setenv("HS_DATABASE_URL", "postgresql://u:p@dpg-x-a/db")
    assert Settings().database_url == "postgresql+psycopg://u:p@dpg-x-a/db"


@pytest.mark.parametrize("given, expected", [("", None), ("false", False), ("true", True)])
def test_slm_think_setting_accepts_empty_as_unset(monkeypatch, given, expected):
    monkeypatch.setenv("HS_SLM_THINK", given)
    assert Settings().slm_think is expected


def test_vectors_are_retried_after_the_embedding_server_was_down(db, monkeypatch):
    s = retrieval.get_settings()
    monkeypatch.setattr(s, "embedding_model", "ollama:fake")
    index = retrieval._Index()
    calls = []

    def fake_vectors(ids, texts, meta):
        calls.append(1)
        if len(calls) == 1:
            return None  # embedding server unreachable
        import numpy as np
        return np.ones((len(ids), 2), dtype="float32")

    monkeypatch.setattr(retrieval, "_load_or_build_vectors", fake_vectors)
    index.ensure(db)
    assert index.vectors is None and len(calls) == 1
    index.ensure(db)                      # still inside the back-off window: no new attempt
    assert len(calls) == 1
    index.retry_vectors_at = 0.0          # back-off elapsed
    index.ensure(db)
    assert len(calls) == 2 and index.vectors is not None and index.retry_vectors_at is None


def test_demo_accounts_doc_is_optional(monkeypatch, tmp_path):
    """The container image has no docs/ folder: writing the accounts list must not abort the seed."""
    from app import demo_seed

    monkeypatch.setattr(demo_seed, "DEMO_ACCOUNTS_DOC", tmp_path / "missing-folder" / "DEMO_ACCOUNTS.md")
    monkeypatch.setattr(demo_seed.get_settings(), "environment", "development")
    demo_seed.write_demo_accounts({"Raipur": {"teacher": "DEMO-T-001", "school": "S"}})  # no exception
