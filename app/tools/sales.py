"""Sales aggregation and reorder arithmetic.

Every number an order recommendation rests on is computed here. The model reads
the result and explains it; it does not do the multiplication.

The multipliers are POLICY RULES lifted from RAG-004, not coefficients fitted
to history, and each one is applied only where the manual says it applies.
"Multiply beer keg baseline orders by 1.5x for a live football broadcast" is a
rule about kegs. Applying it to the gin because there is football on the
television invents a policy the manual does not contain, and an agent quoting
RAG-004 for that number would be citing a document that does not say it.
"""
import datetime
import math

from app import data, db
from app.tools.inventory import _evidence, get_inventory, serving_ml

WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]

# RAG-004, verbatim in effect.
LIVE_FOOTBALL_MULTIPLIER = 1.5   # beer kegs only
VIP_BOOKING_MULTIPLIER = 2.0     # premium spirits only
HOLIDAY_MULTIPLIER = 1.3         # every category

# RAG-004 gives Hendrick's as its example of a premium spirit and no list. Its
# price is the boundary: every spirit at or above it is one of the seven the
# venue would call premium, and nothing cheaper creeps in. A threshold has to
# come from somewhere, and the manual's own example is the honest place.
PREMIUM_SPIRIT_PRICE = 350

VIP_RESERVATION_TYPES = {"private_event", "birthday"}

DEFAULT_WEEKS = 8


# ------------------------------------------------------------------ helpers

def _weekday(day):
    return WEEKDAY_NAMES[datetime.date.fromisoformat(day).weekday()]


def _is_premium_spirit(p):
    from app.tools.inventory import SPIRIT_CATS
    return (p["category"] in SPIRIT_CATS
            and float(p["unit_price"] or 0) >= PREMIUM_SPIRIT_PRICE)


def _truthy(value):
    """The bundle stores is_live as the string 'yes'; Postgres stores it as a
    boolean. Both have to read the same way or the forecast changes depending
    on which one answered."""
    return str(value).strip().lower() in ("yes", "true", "1", "y")


def _rows(product_id):
    p = db.products_by_id().get(product_id)
    if not p:
        return None
    return p, db.select("sales", item_id=product_id)


# --------------------------------------------------------------- history

def get_sales_history(product_id, weekday=None, weeks=DEFAULT_WEEKS, _rows_=None):
    found = _rows_ or _rows(product_id)
    if found is None:
        return {"ok": False, "product_id": product_id,
                "error": f"{product_id} is not in the catalogue"}
    p, rows = found

    cutoff = (data.anchor_date() - datetime.timedelta(weeks=weeks)).isoformat()
    obs = []
    for s in rows:
        if s["date"] < cutoff:
            continue
        wd = _weekday(s["date"])
        if weekday and wd != weekday:
            continue
        obs.append({"date": s["date"], "weekday": wd,
                    "units": s["units_sold"], "revenue": s["revenue"],
                    "lost_to_stockout": s["lost_sales_due_to_stockout"]})

    obs.sort(key=lambda o: o["date"])
    units = [o["units"] for o in obs]
    return {
        "ok": True,
        "product_id": product_id,
        "name": p["name"],
        "unit": p["unit"],
        "weekday": weekday,
        "weeks": weeks,
        "samples": len(obs),
        "mean_units": round(sum(units) / len(units), 2) if units else 0.0,
        "max_units": max(units) if units else 0,
        # Demand that walked out because the shelf was empty. Dropping it makes
        # the next baseline for the same night quietly too low.
        "total_lost_to_stockout": sum(o["lost_to_stockout"] for o in obs),
        "observations": obs[-12:],
    }


# ------------------------------------------------------------- multipliers

def _broadcasts_by_date():
    grouped = {}
    for row in db.select("broadcasts"):
        grouped.setdefault(row["broadcast_date"], []).append(row)
    return grouped


def _bookings_by_date():
    grouped = {}
    for row in db.select("reservations", status="confirmed"):
        grouped.setdefault(row["date"], []).append(row)
    return grouped


def _holidays_by_date():
    return {row["date"]: row for row in db.select("holidays")}


def _day_multiplier(p, day, broadcasts, bookings, holidays):
    """RAG-004 applied to one date, for one product."""
    multiplier, reasons = 1.0, []

    if p["category"] == "draught_beer":
        football = [b for b in broadcasts.get(day, [])
                    if _truthy(b.get("is_live")) and b.get("sport_type") == "Football"]
        if football:
            multiplier *= LIVE_FOOTBALL_MULTIPLIER
            reasons.append(
                f"{len(football)} live football broadcast(s); RAG-004 applies "
                f"{LIVE_FOOTBALL_MULTIPLIER}x to beer kegs")

    if _is_premium_spirit(p):
        vip = [r for r in bookings.get(day, [])
               if r["reservation_type"] in VIP_RESERVATION_TYPES]
        if vip:
            multiplier *= VIP_BOOKING_MULTIPLIER
            reasons.append(
                f"{len(vip)} confirmed private or birthday booking(s); RAG-004 "
                f"applies {VIP_BOOKING_MULTIPLIER}x to premium spirits")

    if day in holidays:
        multiplier *= HOLIDAY_MULTIPLIER
        reasons.append(f"{holidays[day]['title']} is a holiday; RAG-004 applies "
                       f"{HOLIDAY_MULTIPLIER}x across all categories")

    return multiplier, reasons


# ---------------------------------------------------------------- forecast

def _order_rule(p, supplier):
    """Minimum order and rounding step, in stock units.

    IBBL's rule reads "3 kegs or 5 cases", so which half binds depends on what
    is being ordered rather than on who is selling it.
    """
    case = int(p["case_size"] or 1)
    if supplier is None:
        return case, case, "one full case"

    rule = supplier["min_order_rule"]
    qty = int(supplier["min_order_qty"])
    if rule == "3_kegs_or_5_cases":
        if p["unit"] == "keg":
            return qty, 1, f"{qty} kegs"
        return 5 * case, case, f"5 cases of {case}"
    if rule == "min_cases":
        return qty * case, case, f"{qty} cases of {case}"
    return case, case, f"one full case of {case}"


def forecast_reorder(product_id, horizon_days=3, weeks=DEFAULT_WEEKS):
    found = _rows(product_id)
    if found is None:
        return {"ok": False, "product_id": product_id,
                "error": f"{product_id} is not in the catalogue. "
                         "No order can be recommended for it."}
    p, _ = found

    stock = get_inventory(product_id)
    if not stock["ok"]:
        return stock

    broadcasts = _broadcasts_by_date()
    bookings = _bookings_by_date()
    holidays = _holidays_by_date()
    coverage_end = max(broadcasts) if broadcasts else None
    bookings_end = max(bookings) if bookings else None

    unit_ml = float(p["volume_ml"] or 1000)
    per_serving = serving_ml(p)

    # One weekday mean per weekday touched, computed from rows already fetched.
    baselines = {}
    gross, breakdown, unconfirmed = 0.0, [], False

    for offset in range(horizon_days):
        day = (data.anchor_date() + datetime.timedelta(days=offset)).isoformat()
        wd = _weekday(day)
        if wd not in baselines:
            baselines[wd] = get_sales_history(
                product_id, weekday=wd, weeks=weeks, _rows_=found)["mean_units"]

        base_units = baselines[wd] * per_serving / unit_ml
        multiplier, reasons = _day_multiplier(p, day, broadcasts, bookings, holidays)

        fixtures_known = bool(coverage_end and day <= coverage_end)
        bookings_known = bool(bookings_end and day <= bookings_end)
        if not fixtures_known:
            unconfirmed = True
            reasons.append("no confirmed broadcast schedule for this date, so "
                           "no event uplift has been applied")
        if not bookings_known:
            unconfirmed = True
            reasons.append("no booking data for this date")

        gross += base_units * multiplier
        breakdown.append({
            "date": day, "weekday": wd,
            "baseline_units": round(base_units, 3),
            "baseline_pos_units": baselines[wd],
            "multiplier": round(multiplier, 3),
            "fixtures_confirmed": fixtures_known,
            "bookings_confirmed": bookings_known,
            "reasons": reasons,
        })

    # An order placed but not yet on the shelf. Counting it as absent buys the
    # same stock twice; the Heineken case is 48 units already on the way.
    in_flight = sum(float(o["quantity"]) for o in db.select("orders", product_id=product_id)
                    if not o["actual_delivery_date"]
                    or o["actual_delivery_date"] >= stock["as_of"])

    safety = float(p["safety_stock"])
    net = max(0.0, gross + safety - stock["book_stock"] - in_flight)

    # A forecast rests on book stock, and book stock only knows what was
    # written down. If somebody reported stock leaving since the last count,
    # the comfortable number above is the wrong one to act on.
    reported = _evidence(stock["last_count_date"], stock["as_of"]).get(product_id)
    disputed = bool(reported and (reported["chat"] or reported["shift_reports"]))

    supplier = next((s for s in db.select("suppliers")
                     if s["supplier_id"] == p["supplier_id"]), None)
    minimum, step, minimum_basis = _order_rule(p, supplier)
    order = 0 if net <= 0 else max(minimum, int(math.ceil(net / step) * step))

    return {
        "ok": True,
        "product_id": product_id,
        "name": p["name"],
        "unit": p["unit"],
        "horizon_days": horizon_days,
        "book_stock": stock["book_stock"],
        "last_count_date": stock["last_count_date"],
        "count_is_stale": stock["count_is_stale"],
        "safety_stock": safety,
        "below_safety_stock": stock["below_safety_stock"],
        "gross_need": round(gross, 2),
        "units_already_ordered": in_flight,
        "net_need": round(net, 2),
        "recommended_order": order,
        "case_size": int(p["case_size"] or 1),
        "supplier_id": p["supplier_id"],
        "supplier_name": supplier["name"] if supplier else None,
        "supplier_delivery_days": supplier["delivery_days"] if supplier else None,
        "supplier_minimum": minimum,
        "supplier_minimum_basis": minimum_basis,
        "multiplier_source": "RAG-004",
        "multiplier_is_policy_rule": True,
        "policy": {
            "live_football": {"multiplier": LIVE_FOOTBALL_MULTIPLIER,
                              "applies_to": "draught beer kegs"},
            "private_or_birthday_booking": {"multiplier": VIP_BOOKING_MULTIPLIER,
                                            "applies_to": "premium spirits"},
            "holiday_or_erev_chag": {"multiplier": HOLIDAY_MULTIPLIER,
                                     "applies_to": "all categories"},
            # RAG-004 also lifts garnish orders by 1.5x for a private booking.
            # The catalogue carries no garnish flag, and picking the SKUs by
            # hand would be a guess presented as policy, so it is reported
            # unapplied rather than approximated.
            "not_applied": ["garnish 1.5x on a private booking: the catalogue "
                            "does not mark which products are garnish"],
        },
        "broadcast_coverage_ends": coverage_end,
        "bookings_known_through": bookings_end,
        "horizon_partially_unconfirmed": unconfirmed,
        "daily_breakdown": breakdown,
        "stock_position_disputed": disputed,
        "reports_since_count": (reported["chat"] + reported["shift_reports"]
                                if reported else []),
        "note": (
            "Recommendation only. BarMate cannot place or transmit orders, "
            f"and this rests on a physical count from {stock['last_count_date']}."
            + (" Stock was reported leaving this line since that count without "
               "reaching the books, so the position above is optimistic. "
               "Recount before acting on it." if disputed else "")),
    }
