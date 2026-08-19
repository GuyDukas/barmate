"""External context: what the world is doing on a given night.

Real sources only. Broadcasts are scraped listings that keep the URL they came
from, weather is the Open-Meteo ERA5 archive and holidays come from Hebcal.
Where a source does not reach a date the tool says the date is uncovered; it
never fills the hole with an estimate.

Uncovered and quiet are reported as different things on purpose. "No fixtures
listed for 2026-06-25" and "no fixtures on 2026-06-25" would license opposite
decisions, and only one of them is true.
"""
import datetime

from app import db

WEEKDAYS = ["Monday", "Tuesday", "Wednesday", "Thursday",
            "Friday", "Saturday", "Sunday"]


def _truthy(value):
    return str(value).strip().lower() in ("yes", "true", "1", "y")


def _by_date(table, key="date"):
    grouped = {}
    for row in db.select(table):
        grouped.setdefault(row[key], []).append(row)
    return grouped


def get_context(date_from, date_to):
    try:
        start = datetime.date.fromisoformat(date_from)
        end = datetime.date.fromisoformat(date_to)
    except ValueError as e:
        return {"ok": False, "error": f"not a date: {e}"}
    if end < start:
        return {"ok": False,
                "error": f"{date_to} is before {date_from}; no range to report"}

    broadcasts = _by_date("broadcasts", "broadcast_date")
    bookings = _by_date("reservations")
    weather = {row["date"]: row for row in db.select("weather")}
    holidays = {row["date"]: row for row in db.select("holidays")}

    fixtures_end = max(broadcasts) if broadcasts else None
    bookings_end = max(bookings) if bookings else None
    weather_end = max(weather) if weather else None
    holidays_end = max(holidays) if holidays else None

    days = []
    day = start
    while day <= end:
        key = day.isoformat()
        listings = broadcasts.get(key, [])
        live = [b for b in listings if _truthy(b.get("is_live"))]
        confirmed = [r for r in bookings.get(key, []) if r["status"] == "confirmed"]
        days.append({
            "date": key,
            "weekday": WEEKDAYS[day.weekday()],
            "broadcasts": listings,
            "live_broadcasts": len(live),
            "live_football": len([b for b in live if b.get("sport_type") == "Football"]),
            "fixtures_confirmed": bool(fixtures_end and key <= fixtures_end),
            "confirmed_reservations": len(confirmed),
            "confirmed_covers": sum(int(r["party_size"]) for r in confirmed),
            "bookings_confirmed": bool(bookings_end and key <= bookings_end),
            "weather": weather.get(key),
            "holiday": holidays.get(key),
        })
        day += datetime.timedelta(days=1)

    notes = []
    if fixtures_end and date_to > fixtures_end:
        notes.append(f"Confirmed broadcast data ends {fixtures_end}; later dates "
                     "have no fixture information and none should be assumed.")
    if bookings_end and date_to > bookings_end:
        notes.append(f"Bookings are known through {bookings_end} only.")
    if weather_end and date_to > weather_end:
        notes.append(f"Weather runs to {weather_end}.")
    if holidays_end and not any(d["holiday"] for d in days):
        # A checked calendar and a missing calendar look identical in the
        # output unless one of them says so.
        notes.append(f"No holiday falls in this range; the Hebrew calendar is "
                     f"loaded through {holidays_end}.")

    return {
        "ok": True,
        "days": days,
        "broadcast_coverage_ends": fixtures_end,
        "bookings_known_through": bookings_end,
        "weather_covers": weather_end,
        "holiday_data_covers": holidays_end,
        "notes": " ".join(notes),
    }
