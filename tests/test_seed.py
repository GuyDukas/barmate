"""The database and the offline bundle must agree about what the agent may
know. They are built by different code from the same CSVs, so a drift between
them is silent: tests would pass against the fixture while the deployed agent
saw different rows.
"""
import importlib.util
from pathlib import Path

import pytest

from app import data

ROOT = Path(__file__).resolve().parent.parent


def load_seed():
    spec = importlib.util.spec_from_file_location(
        "seed", ROOT / "scripts" / "seed_supabase.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


seed = load_seed()


def rows_for(table):
    for name, filename, cutoff, mode, drop in seed.TABLES:
        if name == table:
            return seed.read_rows(filename, cutoff, mode, drop)[0]
    raise KeyError(table)


@pytest.mark.parametrize("table,bundle_key,grouped", [
    ("sales", "sales_by_product", True),
    ("inventory_counts", "counts_by_product", True),
    ("orders", "orders_by_product", True),
    ("reservations", "reservations_by_date", True),
    ("staff_schedule", "schedule", False),
    ("shift_reports", "shift_reports", False),
    ("whatsapp_messages", "whatsapp", False),
    ("products", "products", False),
])
def test_seed_row_count_matches_bundle(table, bundle_key, grouped):
    bundle = data.load()[bundle_key]
    expected = (sum(len(g) for g in bundle.values()) if grouped else len(bundle))
    assert len(rows_for(table)) == expected


def test_anchor_cutoffs_are_strict():
    """A row keyed to 2026-06-14 describes a night that has not happened at
    18:00 on the 14th."""
    assert max(r["date"] for r in rows_for("sales")) == "2026-06-13"
    assert max(r["date"] for r in rows_for("inventory_counts")) == "2026-06-10"
    assert max(r["timestamp"] for r in rows_for("whatsapp_messages")) < seed.ANCHOR_TS


def test_forward_looking_tables_are_not_truncated_at_the_anchor():
    assert max(r["date"] for r in rows_for("reservations")) > seed.ANCHOR_DATE
    assert max(r["date"] for r in rows_for("staff_schedule")) > seed.ANCHOR_DATE


def test_incident_labels_never_reach_the_database():
    """incident_id says which messages belong to a planted incident. With it,
    listing every incident is a filter rather than an act of reading."""
    assert all("incident_id" not in r for r in rows_for("whatsapp_messages"))
    assert all("incident_id" not in m for m in data.load()["whatsapp"])


def test_knowledge_titles_are_parsed_not_frontmatter_delimiters():
    docs = seed.read_knowledge()
    assert len(docs) == 14
    assert all(d["title"] and d["title"] != "---" for d in docs)
    assert {d["doc_id"] for d in docs} == {f"RAG-{i:03d}" for i in range(1, 15)}
