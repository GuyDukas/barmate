"""
Export the runtime bundle.

    python -m sim.export_runtime

The deployed application loads THIS and nothing else. Two properties matter:

1. Nothing dated after the anchor is present. Not filtered on read -- absent
   from the file. A visibility rule enforced by a filter is a rule someone
   eventually forgets; a rule enforced by absence cannot be forgotten.

2. Rows arrive pre-grouped. The tool layer does lookups and aggregation over
   Python dicts, never a database round trip. Cold start pays the parse cost
   once and every request after that is memory-speed.

Ground truth is never exported. If the agent can reach it, the evaluation is
worthless.
"""
import csv
import gzip
import json
from collections import defaultdict
from datetime import timedelta
from pathlib import Path

from . import config as C

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "data" / "public"
RUNTIME = ROOT / "data" / "runtime"

ANCHOR_DATE = C.ANCHOR.date().isoformat()
ANCHOR_TS = C.ANCHOR.isoformat()


def read(name):
    path = PUBLIC / name
    if not path.exists():
        return []
    return list(csv.DictReader(open(path, encoding="utf-8")))


def numeric(rows, fields):
    for r in rows:
        for f in fields:
            if f in r and r[f] not in ("", None):
                try:
                    r[f] = float(r[f]) if "." in str(r[f]) else int(r[f])
                except ValueError:
                    pass
    return rows


def build():
    # A record keyed to the anchor DATE describes the night of the 14th, which
    # at 18:00 has not happened. The count for that night is taken at 03:00 on
    # the 15th; the orders the simulator places that day are placed after
    # trading. All of it is the future, so the cut is strict.
    #
    # The night of the 13th is fully visible: its count was taken at 03:00 on
    # the 14th and its closing report filed minutes later, both well before
    # doors open again.
    sales = [r for r in read("sales.csv") if r["date"] < ANCHOR_DATE]
    counts = [r for r in read("inventory_counts.csv") if r["date"] < ANCHOR_DATE]
    orders = [r for r in read("orders.csv") if r["order_date"] < ANCHOR_DATE]
    reports = [r for r in read("shift_reports.csv") if r["date"] < ANCHOR_DATE]
    # incident_id is dropped, not merely ignored. It labels which messages
    # correspond to a planted incident, so shipping it would let an agent list
    # every incident with a filter instead of by reading the chat. That is the
    # thing GT005 to GT008 are meant to measure.
    chat = [{k: v for k, v in r.items() if k != "incident_id"}
            for r in read("whatsapp_messages.csv") if r["timestamp"] <= ANCHOR_TS]

    # Reservations and the rota are FORWARD-LOOKING. A booking for next Friday is
    # known today, so a horizon of upcoming days is legitimately visible. Sales
    # for that day are not.
    horizon = (C.ANCHOR.date() + timedelta(days=10)).isoformat()
    reservations = [r for r in read("reservations.csv") if r["date"] <= horizon]
    schedule = [r for r in read("staff_schedule.csv") if r["date"] <= horizon]
    broadcasts = read("broadcasts.csv")

    numeric(sales, ["units_sold", "lost_sales_due_to_stockout", "revenue"])
    numeric(counts, ["reported_stock"])
    numeric(orders, ["quantity"])
    numeric(reservations, ["party_size"])

    products = numeric(read("products.csv"),
                       ["unit_cost", "unit_price", "volume_ml", "case_size",
                        "safety_stock", "is_draught"])

    # ---- pre-grouped indices, so tools never scan a full table -------------
    sales_by_product = defaultdict(list)
    for s in sales:
        sales_by_product[s["item_id"]].append(s)
    counts_by_product = defaultdict(list)
    for c in counts:
        counts_by_product[c["product_id"]].append(c)
    for v in counts_by_product.values():
        v.sort(key=lambda r: r["date"])
    orders_by_product = defaultdict(list)
    for o in orders:
        orders_by_product[o["product_id"]].append(o)
    reservations_by_date = defaultdict(list)
    for r in reservations:
        reservations_by_date[r["date"]].append(r)
    broadcasts_by_date = defaultdict(list)
    for b in broadcasts:
        broadcasts_by_date[b["broadcast_date"]].append(b)

    knowledge = []
    for f in sorted((PUBLIC / "knowledge").glob("RAG-*.md")):
        text = f.read_text(encoding="utf-8")
        lines = text.splitlines()
        knowledge.append({
            "doc_id": f.stem,
            "title": next((l[7:] for l in lines if l.startswith("title: ")), f.stem),
            "text": text.split("---", 2)[-1].strip(),
        })

    bundle = {
        "anchor": ANCHOR_TS,
        "venue": {"name": C.VENUE_NAME, "city": C.CITY,
                  "timezone": C.TIMEZONE, "simulated": True},
        "products": products,
        "suppliers": read("suppliers.csv"),
        "staff": read("staff.csv"),
        "cocktails": numeric(read("cocktails.csv"), ["price"]),
        "recipes": numeric(read("cocktail_recipes.csv"),
                           ["quantity_ml", "quantity_per_cocktail"]),
        "sales_by_product": dict(sales_by_product),
        "counts_by_product": dict(counts_by_product),
        "orders_by_product": dict(orders_by_product),
        "reservations_by_date": dict(reservations_by_date),
        "broadcasts_by_date": dict(broadcasts_by_date),
        "schedule": schedule,
        "shift_reports": reports,
        "whatsapp": chat,
        "knowledge": knowledge,
        # Both are external signals about the world rather than records of the
        # venue's trading, so neither is cut at the anchor. Holidays are
        # published years ahead and weather runs six days past the anchor,
        # which is what a forecast would legitimately give a manager standing
        # at the pass on Sunday evening. They ship because the database
        # carries them: a tool that reads a table in production and raises
        # KeyError under test is worse than one that cannot read it at all.
        "holidays": numeric(read("holidays.csv"), []),
        "weather": numeric(read("weather.csv"),
                           ["temperature_2m_max", "temperature_2m_min",
                            "precipitation_sum", "wind_speed_10m_max"]),
    }

    RUNTIME.mkdir(parents=True, exist_ok=True)
    raw = json.dumps(bundle, ensure_ascii=False, separators=(",", ":"))
    plain = RUNTIME / "bundle.json"
    plain.write_text(raw, encoding="utf-8")
    with gzip.open(RUNTIME / "bundle.json.gz", "wt", encoding="utf-8") as f:
        f.write(raw)

    # Fail loudly rather than shipping a leak.
    leaks = []
    for s in sales:
        if s["date"] >= ANCHOR_DATE:
            leaks.append(f"sale {s['sale_id']} dated {s['date']}")
    for c in counts:
        if c["date"] >= ANCHOR_DATE:
            leaks.append(f"count {c['count_id']} dated {c['date']}")
    for o in orders:
        if o["order_date"] >= ANCHOR_DATE:
            leaks.append(f"order {o['order_id']} dated {o['order_date']}")
    for r in reports:
        if r["submitted_at"] > ANCHOR_TS:
            leaks.append(f"report {r['report_id']} filed {r['submitted_at']}")
    for c in chat:
        if c["timestamp"] > ANCHOR_TS:
            leaks.append(f"chat {c['timestamp']}")
    if leaks:
        raise SystemExit(f"runtime bundle leaks post-anchor rows: {leaks[:5]}")

    return bundle, plain, RUNTIME / "bundle.json.gz"


if __name__ == "__main__":
    bundle, plain, gz = build()
    print(f"anchor            {bundle['anchor']}")
    print(f"bundle.json       {plain.stat().st_size/1024:,.0f} KB")
    print(f"bundle.json.gz    {gz.stat().st_size/1024:,.0f} KB")
    print()
    for k, v in bundle.items():
        if isinstance(v, list):
            print(f"  {k:24s} {len(v):>7,} rows")
        elif isinstance(v, dict) and k.endswith(("_product", "_date")):
            n = sum(len(x) for x in v.values())
            print(f"  {k:24s} {n:>7,} rows in {len(v):,} groups")
