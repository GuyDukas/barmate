#!/usr/bin/env python3
"""
Fetch the real external context the simulation cannot invent.

    python scripts/fetch_external.py

Two sources, both keyless and free for non-commercial use:

  Open-Meteo ERA5 archive   historical daily weather for Netanya
  Hebcal REST API           Jewish calendar, for Erev Chag detection

Writes data/external/weather.csv and data/external/holidays.csv, after which
`python -m sim.build` will pick them up automatically and enable the weather
and holiday multipliers described in RAG-010 and RAG-004.

Run this from a machine with general internet access. It is deliberately kept
out of the request path: BarMate reads these tables from the database, it does
not call third-party APIs while answering a question. A five minute ceiling on
/api/execute is not the place to discover that someone else's API is slow.
"""
import csv
import json
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "external"

sys.path.insert(0, str(ROOT))
from sim import config as C  # noqa: E402

WEATHER_URL = "https://archive-api.open-meteo.com/v1/archive"
HEBCAL_URL = "https://www.hebcal.com/hebcal"


def get_json(url, params):
    full = f"{url}?{urllib.parse.urlencode(params)}"
    print(f"  GET {full[:110]}...")
    with urllib.request.urlopen(full, timeout=60) as r:
        return json.load(r)


def fetch_weather():
    data = get_json(WEATHER_URL, {
        "latitude": C.LATITUDE,
        "longitude": C.LONGITUDE,
        "start_date": C.SIM_START.isoformat(),
        "end_date": C.SIM_END.isoformat(),
        "daily": "temperature_2m_max,temperature_2m_min,precipitation_sum,wind_speed_10m_max",
        "timezone": C.TIMEZONE,
    })
    d = data["daily"]
    rows = [{
        "date": d["time"][i],
        "temperature_2m_max": d["temperature_2m_max"][i],
        "temperature_2m_min": d["temperature_2m_min"][i],
        "precipitation_sum": d["precipitation_sum"][i],
        "wind_speed_10m_max": d["wind_speed_10m_max"][i],
        "source": "Open-Meteo ERA5 archive",
    } for i in range(len(d["time"]))]
    return rows


def fetch_holidays():
    rows = []
    for year in sorted({C.SIM_START.year, C.SIM_END.year}):
        data = get_json(HEBCAL_URL, {
            "v": 1, "cfg": "json", "maj": "on", "min": "on", "mod": "on",
            "nx": "off", "year": year, "month": "x", "ss": "off", "mf": "off",
            "c": "off", "geo": "none", "lg": "s",
        })
        for item in data.get("items", []):
            date = item.get("date", "")[:10]
            if not (C.SIM_START.isoformat() <= date <= C.SIM_END.isoformat()):
                continue
            rows.append({
                "date": date,
                "title": item.get("title", ""),
                "hebrew": item.get("hebrew", ""),
                "category": item.get("category", ""),
                "yomtov": item.get("yomtov", False),
                "source": "Hebcal REST API (CC-BY-4.0)",
            })
    rows.sort(key=lambda r: r["date"])
    return rows


def write(path, rows):
    OUT.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"  wrote {len(rows):,} rows -> {path.relative_to(ROOT)}")


def main():
    failures = []
    print("weather (Open-Meteo):")
    try:
        write(OUT / "weather.csv", fetch_weather())
    except Exception as e:
        failures.append(f"weather: {e}")
        print(f"  FAILED: {e}")

    print("holidays (Hebcal):")
    try:
        write(OUT / "holidays.csv", fetch_holidays())
    except Exception as e:
        failures.append(f"holidays: {e}")
        print(f"  FAILED: {e}")

    if failures:
        print("\nSome sources failed. The build still runs without them, with the "
              "corresponding multipliers disabled. Nothing is substituted or "
              "estimated in their place.")
        return 1
    print("\nDone. Now run: python -m sim.build")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
