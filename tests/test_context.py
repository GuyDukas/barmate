from app.tools import context


def test_tonight_returns_real_fixtures_with_provenance():
    """Every broadcast is a real listing scraped from livegames.co.il and keeps
    the URL it came from, so a claim about tonight's football is checkable."""
    r = context.get_context("2026-06-14", "2026-06-14")
    day = r["days"][0]
    assert day["live_broadcasts"] == 8
    assert day["broadcasts"]
    assert all(b["source_url"] for b in day["broadcasts"])


def test_confirmed_bookings_exclude_cancellations():
    """Four tables were booked for tonight and one cancelled. Counting the
    cancellation as covers inflates every downstream demand figure."""
    day = context.get_context("2026-06-14", "2026-06-14")["days"][0]
    assert day["confirmed_reservations"] == 3
    assert day["confirmed_covers"] == 10


def test_weather_is_real_and_attributed():
    day = context.get_context("2026-06-14", "2026-06-14")["days"][0]
    assert day["weather"]["temperature_2m_max"] == 27.8
    assert "Open-Meteo" in day["weather"]["source"]


def test_a_quiet_calendar_is_reported_as_checked_not_as_missing():
    """No holiday falls in this week. That is a fact from the Hebcal table, not
    an absence of data, and the two read very differently to an agent deciding
    whether the 1.3x Erev Chag rule applies."""
    r = context.get_context("2026-06-14", "2026-06-17")
    assert all(day["holiday"] is None for day in r["days"])
    assert r["holiday_data_covers"]
    assert "no holiday" in r["notes"].lower()


def test_horizon_beyond_coverage_is_flagged():
    r = context.get_context("2026-06-14", "2026-06-20")
    assert r["broadcast_coverage_ends"] == "2026-06-17"
    assert r["days"][-1]["fixtures_confirmed"] is False
    assert r["days"][0]["fixtures_confirmed"] is True
    assert "2026-06-17" in r["notes"]


def test_a_day_past_every_source_says_so_rather_than_going_quiet():
    """2026-06-25 is past broadcasts, past bookings and past the weather
    archive. An empty day that looks like a calm night is the dangerous
    answer; an empty day labelled unknown is the honest one."""
    day = context.get_context("2026-06-25", "2026-06-25")["days"][0]
    assert day["fixtures_confirmed"] is False
    assert day["bookings_confirmed"] is False
    assert day["weather"] is None


def test_backwards_range_is_rejected_rather_than_returning_nothing():
    r = context.get_context("2026-06-17", "2026-06-14")
    assert r["ok"] is False
