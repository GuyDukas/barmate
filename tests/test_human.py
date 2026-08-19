import csv
from pathlib import Path

import pytest

from app.tools import human

TRUTH = Path(__file__).resolve().parent.parent / "data" / "ground_truth" / "shift_report_truth.csv"


# ----------------------------------------------------------------- claims

def test_ambiguous_report_is_flagged_not_resolved():
    """GT001. SR000286 reads 'bacardi carta blanca 15.0' with no word saying
    whether that is what went or what is left. Both readings are consistent
    with the message and nothing in the data chooses between them."""
    r = human.get_shift_reports(date_from="2026-06-13", date_to="2026-06-13")
    claim = next(c for rep in r["reports"] for c in rep["claims"] if c["ambiguous"])
    assert claim["product_id"] == "P025"
    assert claim["value"] == pytest.approx(15.0)
    assert set(claim["possible_meanings"]) == {"units_used", "units_remaining"}
    assert claim["units_remaining"] is None
    assert claim["units_used"] is None


def test_a_marked_claim_separates_what_went_from_what_is_left():
    """'gordon's we went through like 2.0, think theres 6.5 left' carries both
    figures. Collapsing them to one number throws away the half that
    reconciliation needs."""
    r = human.get_shift_reports(date_from="2026-06-13", date_to="2026-06-13")
    claim = next(c for rep in r["reports"] for c in rep["claims"]
                 if c["product_id"] == "P013")
    assert claim["ambiguous"] is False
    assert claim["units_used"] == pytest.approx(2.0)
    assert claim["units_remaining"] == pytest.approx(6.5)


def test_a_percentage_is_not_a_stock_figure():
    """Several reports close with 'I'm not 100% sure'. Reading that 100 as a
    count would put a phantom hundred units on whatever product was named
    last."""
    r = human.get_shift_reports(date_from="2026-01-25", date_to="2026-01-25")
    for rep in r["reports"]:
        for claim in rep["claims"]:
            for value in (claim["units_used"], claim["units_remaining"], claim["value"]):
                assert value != 100


def test_every_report_extracts_exactly_what_was_written():
    """Checked against data/ground_truth/shift_report_truth.csv for all 286
    reports: the products named, the units used and the units left. This is
    the one place where a parser can be graded rather than eyeballed."""
    truth = {r["report_id"]: r for r in csv.DictReader(TRUTH.open(encoding="utf-8"))}
    reports = human.get_shift_reports(limit=None)["reports"]
    assert len(reports) == 286

    for rep in reports:
        t = truth[rep["report_id"]]
        expected = [p.strip() for p in t["products_reported"].split("|") if p.strip()]
        assert [c["product_id"] for c in rep["claims"]] == expected, rep["report_id"]

        ambiguous = {p.strip() for p in t["ambiguous_products"].split("|") if p.strip()}
        used = dict(x.strip().split("=") for x in t["true_units_used"].split("|") if "=" in x)
        left = dict(x.strip().split("=") for x in t["true_closing_stock"].split("|") if "=" in x)

        for claim in rep["claims"]:
            pid = claim["product_id"]
            if pid in ambiguous:
                assert claim["ambiguous"] is True, f"{rep['report_id']} {pid}"
                continue
            # Staff round and estimate, so the written figure sits near the
            # truth rather than on it. What is graded here is that the right
            # number was pulled out of the right sentence.
            assert claim["units_used"] == pytest.approx(float(used[pid]), abs=0.6)
            assert claim["units_remaining"] == pytest.approx(float(left[pid]), abs=0.6)


def test_reports_can_be_narrowed_to_one_product():
    r = human.get_shift_reports(product_id="P025", date_from="2026-06-01")
    assert r["reports"]
    assert all(any(c["product_id"] == "P025" for c in rep["claims"])
               for rep in r["reports"])


def test_an_unknown_product_filter_returns_nothing_not_everything():
    assert human.get_shift_reports(product_id="P999")["count"] == 0


# ------------------------------------------------------------------- chat

def test_chat_surfaces_the_keg_removal():
    r = human.get_chat("2026-06-13", "2026-06-13")
    assert any("קרלסברג 30 ליטר" in m["message"] for m in r["messages"])


def test_chat_can_be_narrowed_to_a_product_in_hebrew():
    """The chat is written in Hebrew and the catalogue query will arrive in
    English. A filter that only matches the English name finds nothing."""
    r = human.get_chat(product_id="K003")
    assert r["count"] == 1
    assert "מהמדף לאירוע" in r["messages"][0]["message"]


def test_the_bottle_does_not_inherit_the_kegs_chat():
    """The bottle is talked about on its own ('the Carlsberg is going well
    tonight'), so it is not silent. What it must not pick up is the message
    about the 30-litre keg, whose Hebrew name contains its own."""
    bottle = human.get_chat(product_id="P001")
    assert bottle["count"] > 0
    assert not any("מהמדף לאירוע" in m["message"] for m in bottle["messages"])


def test_chat_carries_no_incident_labels():
    """The generator tags which messages belong to a planted incident. If that
    column ever reached the runtime data, an agent could list every incident
    with a filter instead of by reading the chat, which is the thing the
    conflict scenarios are meant to measure."""
    for message in human.get_chat()["messages"]:
        assert "incident_id" not in message
