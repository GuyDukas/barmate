"""
BarMate simulation configuration.

Every value here is a modelling choice for a SIMULATED venue. Nothing in this
file is a claim about a real bar. External context (sports broadcasts, weather,
Jewish calendar) is NOT simulated and is sourced separately -- see
scripts/fetch_external.py and data/public/broadcasts.csv.
"""
from datetime import date, datetime

# ---------------------------------------------------------------- simulation window
SIM_START = date(2025, 9, 1)
SIM_END = date(2026, 6, 20)

# The single frozen "now" the agent operates at. Chosen because it is the only
# date where we hold REAL broadcast data for tonight and the following days
# (Sport5 / livegames, 2026-06-14 .. 2026-06-17).
ANCHOR = datetime(2026, 6, 14, 18, 0)

# Everything strictly after ANCHOR exists in the ledger but is withheld from the
# agent. It is the held-out truth used to score forecasts.
VISIBILITY_CUTOFF = ANCHOR

# ---------------------------------------------------------------- venue
VENUE_NAME = "BarMate Demo Venue"
CITY = "Netanya"
LATITUDE = 32.3215
LONGITUDE = 34.8532
TIMEZONE = "Asia/Jerusalem"

OPEN_HOUR = 18
CLOSE_HOUR = 3  # next day

# RAG-005: 1+1 runs 18:00-20:30. POS rings 1x revenue, physical depletion is 2x.
HAPPY_HOUR_START = "18:00"
HAPPY_HOUR_END = "20:30"
HAPPY_HOUR_SHARE = 0.22  # fraction of nightly volume sold inside the window

# ---------------------------------------------------------------- demand shape
# Israeli week: Sunday is a working day, the weekend is Thursday night through
# Saturday. Index 0 = Monday (datetime.weekday()).
DOW_FACTOR = {
    0: 0.55,  # Monday
    1: 0.60,  # Tuesday
    2: 0.75,  # Wednesday
    3: 1.15,  # Thursday, start of the Israeli going-out weekend
    4: 1.45,  # Friday
    5: 1.35,  # Saturday
    6: 0.70,  # Sunday
}

# Mild seasonal lift into summer. Multiplies the daily baseline.
SEASON_PEAK_MONTH = 7
SEASON_AMPLITUDE = 0.18

# Night-to-night randomness that is NOT explained by any signal the agent can
# see. Keeps forecasting honest: a perfect agent still cannot score perfectly.
DAILY_NOISE_SD = 0.11

# Reservations already correlate with the day of week, so treating each cover as
# additive would count Friday twice. Only the DEVIATION from the night's
# expected cover count moves demand, and its influence is bounded.
COVER_SENSITIVITY = 0.30
COVER_UPLIFT_BOUNDS = (0.85, 1.30)
EXPECTED_PARTY_SIZE = 4.3
RESERVATIONS_PER_DOW_UNIT = 8.0

# ---------------------------------------------------------------- inventory policy
COUNT_WEEKDAYS = {2, 6}  # physical stock count on Wednesday and Sunday
ORDER_LEAD_DAYS_NOMINAL = 1
DELAY_PROBABILITY = 0.08
DELAY_EXTRA_DAYS = (1, 3)

# Reported counts differ from truth. Counting sealed bottles is close to exact;
# the uncertainty is the open bottle on the rack, which gets eyeballed. So the
# error is not uniform noise: it is usually nothing, sometimes a bad estimate of
# one open bottle, and occasionally a whole bottle overlooked.
#
# This matters beyond realism. A flat +/-2 noise floor would bury a dropped
# bottle (1.0 units) and a walkout (~0.5 units) below the detection threshold,
# making those incidents unfindable by any agent, however good.
COUNT_ERROR_MODES = [
    (0.90, 0.0, 0.0),    # counted right
    (0.08, 0.05, 0.30),  # open bottle misjudged
    (0.02, 1.00, 1.00),  # a bottle missed entirely
]

# ---------------------------------------------------------------- volumes (RAG-001)
ML_SPIRIT_BOTTLE = 700
ML_HOUSE_BOTTLE = 1000
ML_WINE_BOTTLE = 750
ML_SHOT = 60
ML_CHASER = 30
ML_KEG_30 = 30_000
ML_KEG_50 = 50_000
ML_BEER_SERVING = 330

RANDOM_SEED = 20260614
