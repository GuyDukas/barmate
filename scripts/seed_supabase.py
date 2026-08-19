#!/usr/bin/env python3
"""
Load the generated ledger into Supabase.

    python scripts/seed_supabase.py            # seed everything
    python scripts/seed_supabase.py --check    # count rows, write nothing

Run db/schema.sql in the Supabase SQL Editor first.

The anchor cutoff is applied here rather than in the tools that read the data
later. A visibility rule enforced by every query remembering to add a filter is
a rule that eventually leaks; enforced once at write time, the database simply
does not contain the future.

Sales, counts, orders, reports and chat stop at the anchor. Reservations, the
rota and the fixture list deliberately do not: a booking for next Friday is
genuinely known on the Sunday before. Filtering those would be as wrong as
failing to filter the others.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
PUBLIC = ROOT / "data" / "public"
KNOWLEDGE = PUBLIC / "knowledge"
TIMEOUT = 120
BATCH = 500

ANCHOR_DATE = "2026-06-14"
ANCHOR_TS = "2026-06-14T18:00:00"

HORIZON = "2026-06-24"  # anchor + 10 days

# Cutoff modes, matching sim/export_runtime.build() exactly. The bundle is the
# reference implementation of the visibility rule and these must not diverge
# from it, or the offline fixture and the database disagree about what the
# agent is allowed to know.
#
#   PAST     strict. A row keyed to 2026-06-14 describes the night of the 14th,
#            which at 18:00 has not happened. Its count is taken at 03:00 on
#            the 15th and its orders are placed after trading.
#   MOMENT   inclusive. Timestamps are precise, so anything up to 18:00 counts.
#   FORWARD  a booking for next Friday is genuinely known today.
#   ALL      reference data with no time dimension.
PAST, MOMENT, FORWARD, ALL = "past", "moment", "forward", "all"

# table -> (csv file, cutoff column, mode, columns to drop)
TABLES = [
    ("suppliers", "suppliers.csv", None, ALL, ()),
    ("products", "products.csv", None, ALL, ()),
    ("staff", "staff.csv", None, ALL, ()),
    ("cocktails", "cocktails.csv", None, ALL, ()),
    ("cocktail_recipes", "cocktail_recipes.csv", None, ALL, ()),
    ("sales", "sales.csv", "date", PAST, ()),
    ("inventory_counts", "inventory_counts.csv", "date", PAST, ()),
    ("orders", "orders.csv", "order_date", PAST, ()),
    ("shift_reports", "shift_reports.csv", "date", PAST, ()),
    ("reservations", "reservations.csv", "date", FORWARD, ()),
    ("staff_schedule", "staff_schedule.csv", "date", FORWARD, ()),
    # incident_id labels which messages belong to a planted incident. Loading
    # it would let an agent list every incident with a filter instead of by
    # reading the chat, which is the thing being measured.
    ("whatsapp_messages", "whatsapp_messages.csv", "timestamp", MOMENT,
     ("incident_id",)),
    ("broadcasts", "broadcasts.csv", None, ALL, ()),
    ("weather", "weather.csv", None, ALL, ()),
    ("holidays", "holidays.csv", None, ALL, ()),
]

NUMERIC = {
    "min_order_qty", "volume_ml", "unit_cost", "unit_price", "case_size",
    "safety_stock", "price", "quantity_ml", "quantity_per_cocktail",
    "units_sold", "lost_sales_due_to_stockout", "revenue", "reported_stock",
    "quantity", "party_size", "temperature_2m_max", "temperature_2m_min",
    "precipitation_sum", "wind_speed_10m_max",
}
# Declared integer in the schema, so a float would be rejected outright:
# Postgres will not accept 6.0 for an integer column.
INTEGER = {"party_size"}
BOOLEAN = {"is_draught", "is_live", "yomtov"}
TRUE = {"1", "true", "yes", "y", "t"}


def env():
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        sys.exit("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set. See .env.example")
    return url.rstrip("/"), key


def headers(key, prefer=None):
    h = {"apikey": key, "Authorization": f"Bearer {key}",
         "Content-Type": "application/json"}
    if prefer:
        h["Prefer"] = prefer
    return h


def coerce(row, drop):
    out = {}
    for column, value in row.items():
        if column in drop:
            continue
        value = (value or "").strip()
        if value == "":
            out[column] = None
        elif column in BOOLEAN:
            out[column] = value.lower() in TRUE
        elif column in INTEGER:
            try:
                out[column] = int(float(value))
            except ValueError:
                out[column] = None
        elif column in NUMERIC:
            try:
                out[column] = float(value)
            except ValueError:
                out[column] = None
        else:
            out[column] = value
    return out


def visible(value, mode):
    if mode == ALL:
        return True
    if mode == PAST:
        return value < ANCHOR_DATE
    if mode == MOMENT:
        return value <= ANCHOR_TS
    if mode == FORWARD:
        return value <= HORIZON
    raise ValueError(f"unknown cutoff mode {mode!r}")


def read_rows(filename, cutoff_column, mode, drop):
    with open(PUBLIC / filename, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    kept = [coerce(r, drop) for r in rows
            if visible((r.get(cutoff_column) or ""), mode)]
    return kept, len(rows) - len(kept)


def read_knowledge():
    """Parse the YAML frontmatter for the title. Falling back to the first
    non-blank line would return the '---' delimiter."""
    docs = []
    for path in sorted(KNOWLEDGE.glob("RAG-*.md")):
        raw = path.read_text(encoding="utf-8")
        title, body = None, raw
        if raw.startswith("---"):
            _, _, rest = raw.partition("---")
            front, delimiter, body = rest.partition("---")
            if delimiter:
                for line in front.splitlines():
                    key, _, value = line.partition(":")
                    if key.strip() == "title":
                        title = value.strip()
                body = body.lstrip("\n")
            else:
                body = raw
        if not title:
            heading = next((l for l in body.splitlines() if l.startswith("#")), "")
            title = heading.lstrip("#").strip()
            if ":" in title:
                title = title.split(":", 1)[1].strip()
        docs.append({"doc_id": path.stem, "title": title or path.stem,
                     "text": body.strip()})
    return docs


CLEAR_COLUMN = {
    "suppliers": "supplier_id", "products": "product_id", "staff": "staff_id",
    "cocktails": "cocktail_id", "cocktail_recipes": "cocktail_id",
    "sales": "sale_id", "inventory_counts": "count_id", "orders": "order_id",
    "reservations": "reservation_id", "staff_schedule": "schedule_id",
    "shift_reports": "report_id", "whatsapp_messages": "id",
    "broadcasts": "id", "weather": "date", "holidays": "date",
    "knowledge": "doc_id",
}


def clear(url, key, tables):
    """Empty the tables so seeding is idempotent. Children first: orders and
    counts carry foreign keys into products, which cannot be emptied first."""
    for table in reversed(tables):
        column = CLEAR_COLUMN[table]
        r = requests.delete(f"{url}/rest/v1/{table}",
                            params={column: "not.is.null"},
                            headers=headers(key, "return=minimal"), timeout=TIMEOUT)
        if r.status_code >= 400:
            sys.exit(f"clearing {table}: HTTP {r.status_code}\n{r.text[:400]}")


def insert(url, key, table, rows):
    endpoint = f"{url}/rest/v1/{table}"
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        r = requests.post(endpoint, headers=headers(key, "return=minimal"),
                          data=json.dumps(chunk, ensure_ascii=False).encode("utf-8"),
                          timeout=TIMEOUT)
        if r.status_code >= 400:
            sys.exit(f"\n{table}: HTTP {r.status_code}\n{r.text[:600]}\n"
                     f"first row of failing batch: {chunk[0]}")


def count(url, key, table):
    r = requests.get(f"{url}/rest/v1/{table}", params={"select": "*", "limit": 1},
                     headers=headers(key, "count=exact"), timeout=TIMEOUT)
    if r.status_code >= 400:
        return None
    content_range = r.headers.get("content-range", "")
    return content_range.split("/")[-1] if "/" in content_range else "?"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="report row counts already in Supabase, write nothing")
    args = ap.parse_args()
    url, key = env()

    if args.check:
        print()
        for table, *_ in TABLES:
            print(f"  {table:<20} {count(url, key, table)}")
        print(f"  {'knowledge':<20} {count(url, key, 'knowledge')}\n")
        return 0

    names = [t[0] for t in TABLES] + ["knowledge"]
    print("\n  clearing existing rows...")
    clear(url, key, names)
    print()
    total, total_cut = 0, 0
    for table, filename, cutoff, mode, drop in TABLES:
        rows, cut = read_rows(filename, cutoff, mode, drop)
        insert(url, key, table, rows)
        note = f"  ({cut} beyond the anchor withheld)" if cut else ""
        note += "  [incident_id dropped]" if drop else ""
        print(f"  {table:<20} {len(rows):>6,}{note}")
        total += len(rows)
        total_cut += cut

    docs = read_knowledge()
    insert(url, key, "knowledge", docs)
    print(f"  {'knowledge':<20} {len(docs):>6,}")
    total += len(docs)

    print(f"\n  {total:,} rows loaded, {total_cut:,} withheld as after the anchor.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
