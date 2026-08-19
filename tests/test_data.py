import datetime

from app import data


def test_bundle_loads_and_is_cached():
    a = data.load()
    b = data.load()
    assert a is b, "bundle must be loaded once and reused across requests"


def test_anchor_is_frozen():
    assert data.anchor() == datetime.datetime(2026, 6, 14, 18, 0)


def test_no_record_leaks_past_the_anchor():
    b = data.load()
    sales = [s for g in b["sales_by_product"].values() for s in g]
    counts = [c for g in b["counts_by_product"].values() for c in g]
    assert max(s["date"] for s in sales) == "2026-06-13"
    assert max(c["date"] for c in counts) == "2026-06-10"
    assert max(m["timestamp"] for m in b["whatsapp"]) < "2026-06-14T18:00:00"


def test_forward_looking_records_are_present():
    b = data.load()
    assert max(b["reservations_by_date"]) == "2026-06-20"
    assert max(b["broadcasts_by_date"]) == "2026-06-17"


def test_catalog_shape():
    b = data.load()
    assert len(b["products"]) == 61
    assert len(b["knowledge"]) == 14
    assert sum(1 for p in b["products"] if p["is_draught"]) == 5
