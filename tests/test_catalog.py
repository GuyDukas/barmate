from app.tools import catalog


def test_exact_match():
    r = catalog.resolve_product("Jameson")
    assert r["found"] is True
    assert r["matches"][0]["product_id"] == "P019"


def test_hebrew_match():
    r = catalog.resolve_product("ג'יימסון")
    assert r["found"] is True
    assert r["matches"][0]["product_id"] == "P019"


def test_partial_match_returns_candidates():
    r = catalog.resolve_product("carlsberg")
    ids = {m["product_id"] for m in r["matches"]}
    assert {"P001", "K001", "K003"} <= ids


def test_unknown_product_is_not_guessed():
    r = catalog.resolve_product("Macallan")
    assert r["found"] is False
    assert r["matches"] == []
    assert "not in the catalogue" in r["note"]


def test_exact_match_ranks_first_without_hiding_partials():
    """The bottle matches exactly and the kegs partially. Both must come back,
    exact first. GT004 turns on the 30L keg, which a bottle-only answer hides.
    """
    r = catalog.resolve_product("carlsberg")
    assert r["matches"][0]["product_id"] == "P001"
    assert {"K001", "K003"} <= {m["product_id"] for m in r["matches"]}


def test_category_lookup():
    r = catalog.resolve_category("gin")
    assert len(r["products"]) == 5
