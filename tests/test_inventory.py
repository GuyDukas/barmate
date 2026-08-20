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


def test_the_envelope_says_why_a_line_is_wide():
    """GT006. The agent asked for House White Wine's variance, got 4.2, and
    correctly concluded it was not theft -- but could not say why the tolerance
    is so wide, because nothing told it. The envelope knows: this is a
    happy-hour line, and RAG-005 is the document that explains it."""
    wine = inventory.variance_envelope("P039")
    assert wine["happy_hour_line"] is True
    assert "RAG-005" in wine["explanation_docs"]

    gin = inventory.variance_envelope("P010")
    assert gin["happy_hour_line"] is False
    assert gin["explanation_docs"] == []


def test_stock_position_names_the_protocol_that_widens_the_line():
    """GT006. Every stock question goes through get_inventory, and the agent
    kept concluding 'possible mismatch' on House White Wine without ever
    reaching the document that explains why that line runs short. The fact is
    a property of the product and costs nothing to carry."""
    wine = inventory.get_inventory("P039")
    assert wine["happy_hour_line"] is True
    assert "RAG-005" in wine["explanation_docs"]

    gin = inventory.get_inventory("P010")
    assert gin["happy_hour_line"] is False
    assert gin["explanation_docs"] == []


def test_a_counted_figure_on_a_two_bar_line_carries_a_scope_warning():
    """Reported live: a bartender said "3 left on the shelf" for a line
    carried at both bars, and a venue-wide book figure of 45.4 turned that
    into 42.4 units of unexplained shrinkage -- an accusation manufactured
    out of the difference between one shelf and the whole venue."""
    r = inventory.reconcile("P007", physical_stock=3)
    assert r["station"] == "both"
    assert "RAG-007" in r["scope_note"]
    # And it must not suppress the arithmetic while it asks.
    assert r["gap_units"] is not None
    assert r["book_stock"] == 45.4


def test_a_single_bar_line_needs_no_scope_warning():
    """Carlsberg 30L lives at the outside bar and nowhere else, so a count
    of it is not ambiguous about which bar was counted."""
    r = inventory.reconcile("K003", physical_stock=1)
    assert r["station"] == "outside"
    assert "scope_note" not in r


def test_no_counted_figure_means_no_scope_question():
    """Nothing was counted, so there is no count whose scope could be wrong."""
    assert "scope_note" not in inventory.reconcile("P007")


def test_find_discrepancies_defaults_to_the_unverified_stretch():
    """The default is what an unqualified "what have we lost" means when
    nothing has been counted for four days: only what somebody wrote down,
    and no settled arithmetic, because no window has closed."""
    d = inventory.find_discrepancies()
    assert d["window"] == ["2026-06-10", "2026-06-14"]
    assert d["logged"]
    assert d["counted_windows"] == []


def test_a_month_reaches_the_windows_that_were_actually_counted():
    """Reported: the agent would not look back further than the last count.
    A month holds eight closed counts, and each one is settled arithmetic
    that needed nobody to report it."""
    m = inventory.find_discrepancies(date_from="2026-05-14")
    assert m["window"] == ["2026-05-14", "2026-06-14"]
    assert m["counted_windows_found"] > 20
    assert all(w["from"] >= "2026-05-14" for w in m["counted_windows"])


def test_only_windows_that_broke_their_own_line_are_returned():
    """Every product has a residual on every window. A two-unit miss is noise
    on Coca-Cola and a crisis on Tanqueray, so the envelope decides."""
    m = inventory.find_discrepancies(date_from="2026-05-14")
    assert all(abs(w["residual"]) > w["envelope"] for w in m["counted_windows"])
    assert all(w["times_envelope"] > 1 for w in m["counted_windows"])


def test_the_worst_offenders_come_first_and_the_rest_are_counted():
    m = inventory.find_discrepancies(date_from="2026-05-14")
    ratios = [w["times_envelope"] for w in m["counted_windows"]]
    assert ratios == sorted(ratios, reverse=True)
    assert len(m["counted_windows"]) <= inventory.COUNTED_WINDOW_LIMIT
    assert m["counted_windows_found"] >= len(m["counted_windows"])


def test_the_payload_survives_the_observation_limit():
    """A month of breaches is eight thousand characters unabridged, and the
    loop cuts an observation at three and a half thousand -- which would hand
    the model a record sliced in half with nothing saying so."""
    import json
    from app.agent.loop import OBSERVATION_CHAR_LIMIT
    for kwargs in ({}, {"date_from": "2026-05-14"}, {"date_from": "2025-09-01"}):
        payload = json.dumps(inventory.find_discrepancies(**kwargs),
                             ensure_ascii=False, default=str)
        assert len(payload) <= OBSERVATION_CHAR_LIMIT, f"{kwargs}: {len(payload)}"


def test_the_note_says_how_far_the_chat_actually_reaches():
    """Silence from a source that was not listening is not evidence of quiet.
    The chat starts on 2026-06-01, so a question about April finds nothing
    written down and that must not read as nothing happening."""
    note = inventory.find_discrepancies(date_from="2026-04-01")["note"]
    assert "2026-06-01" in note


def test_a_category_reports_every_line_in_one_call():
    """Observed against the deployment: "and the whisky?" needed a figure and
    an envelope for six bottles, twelve calls against an eight-iteration cap,
    and the agent got three quarters of the way through and stopped."""
    r = inventory.get_category_inventory("whiskey")
    assert r["ok"]
    assert len(r["products"]) == 6
    assert all(p["book_stock"] is not None for p in r["products"])
    assert all(p["expected_variance"] > 0 for p in r["products"])


def test_a_misspelled_category_still_resolves():
    """The catalogue spells it whiskey. A manager asking about whisky is
    asking about the same six bottles."""
    r = inventory.get_category_inventory("whisky")
    assert r["ok"]
    assert r["category"] == "whiskey"
    assert len(r["products"]) == 6


def test_beer_says_it_is_only_half_the_beer():
    """Bottles and kegs are two categories at a venue with draught lines, and
    a stock answer covering one of them has answered half the question."""
    r = inventory.get_category_inventory("beer")
    assert r["incomplete"]
    assert r["missing_categories"] == ["draught_beer"]
    assert "draught_beer" in r["note"]


def test_a_category_flags_the_lines_nobody_can_vouch_for():
    r = inventory.get_category_inventory("whiskey")
    assert set(r["disputed"]) == {"P019", "P021"}
    assert all(p["stock_position_disputed"] for p in r["products"]
               if p["product_id"] in r["disputed"])


def test_a_category_payload_fits_the_observation_limit():
    """The whole point is to answer in one call. A payload the loop truncates
    would put the agent back where it started, minus the iterations."""
    import json
    from app import db
    from app.agent.loop import OBSERVATION_CHAR_LIMIT
    for category in sorted({p["category"] for p in db.products()}):
        payload = json.dumps(inventory.get_category_inventory(category),
                             ensure_ascii=False, default=str)
        assert len(payload) <= OBSERVATION_CHAR_LIMIT, f"{category}: {len(payload)}"


def test_an_unknown_category_is_refused_with_the_real_ones():
    r = inventory.get_category_inventory("absinthe")
    assert r["ok"] is False
    assert "whiskey" in r["available_categories"]
