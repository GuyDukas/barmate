import pytest

from app.tools import sales


# ---------------------------------------------------------------- history

def test_weekday_baseline_uses_matching_weekdays_only():
    r = sales.get_sales_history("K003", weekday="Sunday", weeks=8)
    assert r["samples"] == 8
    assert r["mean_units"] == pytest.approx(41.75, abs=0.01)
    assert all(o["weekday"] == "Sunday" for o in r["observations"])


def test_history_reports_sales_lost_to_stockouts():
    """Units that could not be sold because the shelf was empty are demand,
    not absence of demand. A baseline that quietly drops them under-orders the
    next time the same night comes round."""
    r = sales.get_sales_history("K003", weeks=8)
    assert "total_lost_to_stockout" in r
    assert r["samples"] > 40


def test_unknown_product_has_no_history():
    assert sales.get_sales_history("P999")["ok"] is False


# ------------------------------------------------------------- multipliers

def test_football_multiplier_applies_to_kegs_and_cites_the_policy():
    r = sales.forecast_reorder("K003", horizon_days=1)
    day = r["daily_breakdown"][0]
    assert day["multiplier"] == pytest.approx(1.5)
    assert any("football" in reason.lower() for reason in day["reasons"])
    assert r["multiplier_source"] == "RAG-004"
    assert r["multiplier_is_policy_rule"] is True


def test_football_multiplier_does_not_touch_the_gin():
    """RAG-004 multiplies 'beer keg baseline orders' by 1.5 on a live football
    broadcast. It says nothing about gin. Applying it to every product because
    there is football on the television invents a policy the manual does not
    contain."""
    r = sales.forecast_reorder("P009", horizon_days=1)
    assert r["daily_breakdown"][0]["multiplier"] == pytest.approx(1.0)


def test_vip_and_birthday_bookings_lift_premium_spirits_only():
    """RAG-004 doubles premium spirits for a private or birthday booking.
    2026-06-15 carries a confirmed birthday; Hendrick's is the manual's own
    example of a premium spirit, and Bombay Sapphire is not one."""
    premium = sales.forecast_reorder("P011", horizon_days=2)
    ordinary = sales.forecast_reorder("P009", horizon_days=2)
    assert premium["daily_breakdown"][1]["multiplier"] == pytest.approx(2.0)
    assert ordinary["daily_breakdown"][1]["multiplier"] == pytest.approx(1.0)


def test_no_holiday_in_the_horizon_leaves_the_baseline_alone():
    """The 1.3x Erev Chag rule is real and the holiday table is real. Nothing
    falls in this horizon, so the multiplier must stay at 1.0 rather than be
    asserted either way from memory."""
    r = sales.forecast_reorder("P009", horizon_days=7)
    assert all(not any("holiday" in reason.lower() for reason in day["reasons"])
               for day in r["daily_breakdown"])


# --------------------------------------------------------------- forecast

def test_baseline_converts_pos_servings_into_stock_units():
    """The till counts 330ml pours; the cold room counts 30-litre kegs."""
    r = sales.forecast_reorder("K003", horizon_days=1)
    assert r["daily_breakdown"][0]["baseline_units"] == pytest.approx(
        41.75 * 330 / 30000, abs=0.01)


def test_forecast_subtracts_orders_already_in_flight():
    """Heineken has 48 units on a delayed order due 2026-06-15. Recommending
    a fresh order that ignores them buys the same stock twice."""
    r = sales.forecast_reorder("P002", horizon_days=3)
    assert r["units_already_ordered"] == 48
    assert r["net_need"] == pytest.approx(
        max(0.0, r["gross_need"] + r["safety_stock"]
            - r["book_stock"] - r["units_already_ordered"]), abs=0.01)


def test_forecast_respects_the_ibbl_keg_minimum():
    r = sales.forecast_reorder("K003", horizon_days=10)
    assert r["supplier_id"] == "SUP02"
    assert r["supplier_minimum"] == 3
    assert r["supplier_delivery_days"] == "Tue,Fri"
    assert r["recommended_order"] % 1 == 0
    assert r["recommended_order"] == 0 or r["recommended_order"] >= 3


def test_bottled_beer_from_the_same_supplier_takes_the_case_minimum():
    """IBBL's rule is three kegs OR five cases. Bottled Heineken is not a keg,
    so the three does not apply to it."""
    r = sales.forecast_reorder("P002", horizon_days=3)
    assert r["supplier_minimum"] == 5 * 24
    assert r["recommended_order"] % 24 == 0


def test_forecast_declares_an_unconfirmed_horizon():
    """Broadcast coverage stops on 2026-06-17. Past that the tool must say the
    fixtures are unknown rather than let the model extrapolate a football
    night that nobody has confirmed."""
    r = sales.forecast_reorder("K003", horizon_days=10)
    assert r["broadcast_coverage_ends"] == "2026-06-17"
    assert r["horizon_partially_unconfirmed"] is True
    beyond = [d for d in r["daily_breakdown"] if d["date"] > "2026-06-17"]
    assert beyond and all(d["fixtures_confirmed"] is False for d in beyond)
    assert all(d["multiplier"] == pytest.approx(1.0) for d in beyond)


def test_forecast_refuses_to_place_the_order():
    r = sales.forecast_reorder("K003", horizon_days=3)
    assert "cannot" in r["note"].lower()


def test_forecast_carries_the_staleness_of_the_count_it_rests_on():
    """The recommendation is only as good as the last physical count, and that
    count is four days old. A number presented without that caveat reads as
    firmer than it is."""
    r = sales.forecast_reorder("K003", horizon_days=3)
    assert r["count_is_stale"] is True
    assert r["book_stock"] == pytest.approx(6.08, abs=0.02)


def test_unknown_product_gets_no_recommendation():
    r = sales.forecast_reorder("P999")
    assert r["ok"] is False
    assert "recommended_order" not in r


def test_forecast_flags_a_stock_position_the_chat_disputes():
    """GT004's trap. Carlsberg 30L books at six kegs against a floor of two, so
    the arithmetic alone says no order is needed. The group chat says two kegs
    were pulled from the cold room and nobody recorded it. A forecast that
    reports comfort without that caveat sends the outside bar into a football
    night on one keg."""
    disputed = sales.forecast_reorder("K003", horizon_days=1)
    assert disputed["stock_position_disputed"] is True
    assert disputed["reports_since_count"]
    assert "recount" in disputed["note"].lower()


def test_an_undisputed_line_carries_no_such_warning():
    quiet = sales.forecast_reorder("P010", horizon_days=1)
    assert quiet["stock_position_disputed"] is False
    assert quiet["reports_since_count"] == []


def test_a_category_forecast_covers_every_product_in_one_call():
    """GT003. Asked how much beer to order, the agent forecast five of the
    thirteen beer SKUs one call at a time, ran out of iterations and answered
    on a third of the shelf. A category is one question and should cost one
    call."""
    r = sales.forecast_category("draught_beer", horizon_days=3)
    assert r["ok"] is True
    assert len(r["products"]) == 5
    assert {p["product_id"] for p in r["products"]} == {"K001", "K002", "K003",
                                                        "K004", "K005"}
    assert all("recommended_order" in p for p in r["products"])
    assert r["broadcast_coverage_ends"] == "2026-06-17"


def test_a_category_forecast_surfaces_the_disputed_lines():
    r = sales.forecast_category("draught_beer", horizon_days=1)
    assert "K003" in r["disputed"]


def test_an_unknown_category_forecast_says_so():
    r = sales.forecast_category("cheese")
    assert r["ok"] is False
    assert "available_categories" in r


def test_a_bottle_only_beer_forecast_declares_what_it_left_out():
    """The agent resolved 'beer', forecast the eight bottles and answered a
    weekend order question without the five kegs -- most of what a bar with
    draught lines actually sells. A hint at resolve time was ignored; a warning
    inside the result it just read is harder to miss."""
    r = sales.forecast_category("beer")
    assert r["incomplete"] is True
    assert "draught_beer" in r["missing_categories"]
    assert "draught_beer" in r["note"]

    kegs = sales.forecast_category("draught_beer")
    assert kegs["incomplete"] is True
    assert "beer" in kegs["missing_categories"]

    gin = sales.forecast_category("gin")
    assert gin["incomplete"] is False
    assert gin["missing_categories"] == []
