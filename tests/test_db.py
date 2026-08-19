import pytest

from app import db


def test_offline_fixture_serves_tables_without_credentials(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert db.configured() is False
    assert len(db.select("products")) == 61
    assert len(db.select("knowledge")) == 14


def test_grouped_tables_flatten(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    rows = db.select("inventory_counts")
    assert rows and all("product_id" in r for r in rows)


def test_equality_filter(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    gins = db.select("products", category="gin")
    assert len(gins) == 5
    assert all(p["category"] == "gin" for p in gins)


def test_operator_filter(monkeypatch):
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    recent = db.select("sales", date="gte.2026-06-13")
    assert recent
    assert all(r["date"] >= "2026-06-13" for r in recent)


def test_request_path_refuses_the_offline_fixture(monkeypatch):
    """Answering a stock question from a developer's local copy would look
    exactly like a real answer, which is worse than failing."""
    monkeypatch.delenv("SUPABASE_URL", raising=False)
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    with pytest.raises(RuntimeError, match="refusing to answer"):
        db.require_supabase()
