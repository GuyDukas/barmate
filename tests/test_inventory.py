import pytest

from app.tools import inventory


# --------------------------------------------------------------- book stock

def test_book_stock_matches_ground_truth_for_gin():
    r = inventory.get_inventory("P009")
    assert r["ok"] is True
    assert r["last_count_date"] == "2026-06-10"
    assert r["last_count"] == pytest.approx(8.15, abs=0.01)
    assert r["units_sold_since"] == pytest.approx(1.2, abs=0.02)
    assert r["book_stock"] == pytest.approx(6.95, abs=0.02)
    assert r["days_since_count"] == 4


def test_book_stock_believes_the_invoice_not_the_delivery():
    """CBC invoiced ten cases of Coca-Cola and delivered nine (INC-048). The
    books have no way to know that, so book stock must carry the invoiced 240.
    A tool that quietly used the received quantity would erase the very
    discrepancy the agent exists to find."""
    r = inventory.get_inventory("P043")
    assert r["units_invoiced_since"] == pytest.approx(240.0, abs=0.01)
    assert r["book_stock"] == pytest.approx(245.5, abs=0.02)


def test_count_staleness_is_reported():
    r = inventory.get_inventory("P001")
    assert r["days_since_count"] == 4
    assert r["count_is_stale"] is True


def test_unknown_product_returns_error_not_zero():
    """A tool that returns 0 for an unknown product invites the model to report
    zero stock. Absence and emptiness must be distinguishable."""
    r = inventory.get_inventory("P999")
    assert r["ok"] is False
    assert "book_stock" not in r


# ------------------------------------------------------- variance envelope

def test_variance_envelope_is_derived_not_hardcoded():
    """Noise scales with throughput. Tanqueray moves under a bottle between
    counts; Coca-Cola moves thirty. One constant cannot serve both, and a
    threshold near 0.5 sits inside Coca-Cola's ordinary variation."""
    quiet = inventory.variance_envelope("P010")
    busy = inventory.variance_envelope("P043")
    assert quiet["envelope"] < 1.0
    assert busy["envelope"] > 5.0
    assert quiet["windows"] > 20


def test_happy_hour_lines_carry_a_wider_envelope_than_their_peers():
    """RAG-005 doubles physical depletion on house wine while the till rings
    single, so that line is structurally noisier than a shelf gin. The
    envelope has to learn this rather than be told it."""
    house_wine = inventory.variance_envelope("P039")
    shelf_gin = inventory.variance_envelope("P010")
    assert house_wine["envelope"] > shelf_gin["envelope"] * 5


# ------------------------------------------------------------- reconcile

def test_reconcile_without_a_physical_figure_computes_no_gap():
    """The last count is 2026-06-10 and the anchor is 2026-06-14. Nothing has
    been counted since, so there is no independent figure to compare against.
    Inventing one is the failure mode this guards."""
    r = inventory.reconcile("P009")
    assert r["ok"] is True
    assert r["gap_units"] is None
    assert r["classification"] == "not_computable"
    assert "no physical count" in r["note"].lower()


def test_reconcile_flags_the_silent_gin_removal():
    """INC-041: a case cleared for a private function, never written down.
    Nobody mentioned it, so only the arithmetic can catch it."""
    r = inventory.reconcile("P009", physical_stock=0.0)
    assert r["gap_units"] == pytest.approx(6.95, abs=0.05)
    assert r["gap_is_material"] is True
    assert r["classification"] == "unexplained_shrinkage"
    assert r["evidence"]["chat"] == []
    assert r["evidence"]["shift_reports"] == []


def test_reconcile_absorbs_happy_hour_variance_on_house_wine():
    """GT006. House white runs 1.72 units short against a line whose ordinary
    variation is four. Calling that theft would be wrong."""
    r = inventory.reconcile("P039", physical_stock=12.18)
    assert r["gap_units"] == pytest.approx(1.72, abs=0.05)
    assert r["gap_is_material"] is False
    assert r["happy_hour_line"] is True
    assert r["classification"] == "clean"
    assert "RAG-005" in r["explanation_docs"]


def test_happy_hour_does_not_excuse_a_gap_beyond_the_envelope():
    """K003 is a happy-hour line AND the product two kegs were pulled from
    (INC-050). A tool that stops at 'happy hour explains it' buries the keg
    that leaves the outside bar short on a football night."""
    r = inventory.reconcile("K003", physical_stock=1.15)
    assert r["happy_hour_line"] is True
    assert r["gap_units"] == pytest.approx(4.93, abs=0.05)
    assert r["gap_is_material"] is True
    assert r["classification"] != "clean"


def test_reconcile_is_quiet_on_a_clean_product():
    r = inventory.reconcile("P010", physical_stock=6.232)
    assert abs(r["gap_units"]) < 0.05
    assert r["classification"] == "clean"


def test_stock_above_the_books_is_phantom_stock_not_shrinkage():
    """RAG-013 scenario D. More on the shelf than the books allow is a
    miscount or an undocumented delivery, not a loss."""
    r = inventory.reconcile("P010", physical_stock=20.0)
    assert r["gap_units"] < 0
    assert r["classification"] == "phantom_stock"
    assert "RAG-013" in r["explanation_docs"]


# -------------------------------------------------------------- evidence

def test_evidence_is_found_in_the_hebrew_group_chat():
    """The chat is written in Hebrew. Matching only the English catalogue name
    would find nothing and report every logged loss as silent."""
    r = inventory.reconcile("K003", physical_stock=1.15)
    assert any("קרלסברג 30 ליטר" in m["message"] for m in r["evidence"]["chat"])
    assert r["classification"] == "reported_loss"


def test_the_bottle_does_not_inherit_the_kegs_messages():
    """'קרלסברג' is a strict substring of 'קרלסברג 30 ליטר'. A naive contains
    test hands the keg removal to the bottled beer as well, which would
    manufacture an incident on a product nothing happened to."""
    keg = inventory.reconcile("K003", physical_stock=1.15)
    bottle = inventory.reconcile("P001", physical_stock=80.0)
    assert any("מהמדף לאירוע" in m["message"] for m in keg["evidence"]["chat"])
    assert not any("מהמדף לאירוע" in m["message"] for m in bottle["evidence"]["chat"])


# ------------------------------------------------------------ the sweep

def test_find_discrepancies_separates_logged_losses_from_the_blind_spot():
    """GT007. Losses somebody mentioned are findable; losses nobody mentioned
    are not, because nothing has been counted since they happened. The tool
    must say so rather than present a clean sweep."""
    r = inventory.find_discrepancies()
    logged = {row["product_id"] for row in r["logged"]}
    assert {"K003", "P043", "P016"} <= logged
    assert r["unverified_since"] == "2026-06-10"
    assert r["note"]
