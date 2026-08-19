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


class _Response:
    def __init__(self, rows):
        self._rows = rows

    def raise_for_status(self):
        pass

    def json(self):
        return self._rows


def test_select_pages_past_the_postgrest_row_cap(monkeypatch):
    """PostgREST answers an unbounded select with at most 1000 rows and no
    error. The reservations table holds nearly 2000, so a forecast reading it
    saw bookings stop in January and quietly lost every event multiplier after
    that date -- in production only, while the offline suite stayed green."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    pages = [[{"i": n} for n in range(1000)], [{"i": n} for n in range(1000, 1500)]]
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params)
        return _Response(pages[len(calls) - 1])

    monkeypatch.setattr(db._session, "get", fake_get)
    rows = db.select("reservations")
    assert len(rows) == 1500
    assert len(calls) == 2
    assert "offset=1000" in calls[1]


def test_an_explicit_limit_is_honoured_with_one_request(monkeypatch):
    """A caller asking for one row must not trigger a paging loop."""
    monkeypatch.setenv("SUPABASE_URL", "https://example.supabase.co")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    calls = []

    def fake_get(url, headers=None, params=None, timeout=None):
        calls.append(params)
        return _Response([{"date": "2026-06-10"}])

    monkeypatch.setattr(db._session, "get", fake_get)
    rows = db.select("inventory_counts", order="date.desc", limit=1)
    assert len(rows) == 1
    assert len(calls) == 1
