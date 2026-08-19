# BarMate Implementation Plan

> Work this plan task by task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build and deploy an autonomous bar operations agent that answers plain-language questions by choosing its own data sources, reconciling books against human reports, and refusing to act beyond its authority.

**Architecture:** Single agent, ReAct loop with a Reflect gate, no intent router. Ten deterministic tools carry all arithmetic. The full ledger loads from a 250 KB gzipped bundle at cold start in 42 ms, so no request touches a database.

**Tech Stack:** Python 3.12, Flask, Vercel serverless, LLMod.ai (`MB5R2CF-azure/gpt-5.4-mini`, `MB5R2CF-azure/text-embedding-3-small`), pytest. Stdlib only in the request path.

**Read first:** `docs/specs/2026-08-19-barmate-design.md`, `README.md`, `data/DATASET.md`.

---

## File structure

```
api/
  index.py                 Flask app, all four endpoints plus GUI
app/
  data.py                  bundle loader, cached at module scope
  llm.py                   LLMod.ai client, retries, token accounting
  agent/
    loop.py                Reasoner ReAct loop, iteration cap
    reflect.py             Reflector gate and Reviser
    prompts.py             system prompts, one per module
    trace.py               steps accumulator matching the brief's schema
  tools/
    registry.py            name to callable, JSON schemas for the model
    catalog.py             resolve_product
    inventory.py           get_inventory, reconcile
    sales.py               get_sales_history, forecast_reorder
    context.py             get_context
    human.py               get_shift_reports, get_chat
    knowledge.py           search_knowledge
static/
  index.html               GUI, no build step
  architecture.png         generated, served by /api/model_architecture
scripts/
  build_embeddings.py      precompute the 14 doc vectors
  render_architecture.py   generate the PNG
eval/
  run.py                   nine scenarios against ground truth
  metrics.py               parse accuracy, detection recall, forecast error
tests/
  test_data.py  test_catalog.py  test_inventory.py  test_sales.py
  test_context.py  test_human.py  test_knowledge.py  test_registry.py
  test_trace.py  test_endpoints.py
```

Split by responsibility, not layer: everything about stock lives in
`inventory.py`, everything about people's words lives in `human.py`.

---

## Phase 1: Foundation

### Task 1: Repository scaffold and bundle loader

**Files:**
- Create: `app/data.py`
- Create: `tests/test_data.py`
- Create: `requirements.txt`, `vercel.json`, `.vercelignore`

- [ ] **Step 1: Write `requirements.txt`**

```
Flask==3.0.3
requests==2.32.3
Pillow==10.4.0
pytest==8.3.2
```

`Pillow` is used only by `scripts/render_architecture.py`, which runs offline.
Nothing in the request path imports it.

- [ ] **Step 2: Write `.vercelignore`**

```
data/ground_truth/
data/public/
data/external/
sim/
eval/
tests/
docs/
scripts/
*.tar.gz
```

Ground truth reaching the deployed bundle would invalidate every evaluation
number. This file is the only thing preventing that.

- [ ] **Step 3: Write the failing test**

```python
# tests/test_data.py
import datetime
from app import data

def test_bundle_loads_and_is_cached():
    a = data.load()
    b = data.load()
    assert a is b, "bundle must be loaded once and reused across requests"

def test_anchor_is_frozen():
    assert data.anchor() == datetime.datetime(2026, 6, 14, 18, 0)

def test_no_record_leaks_past_the_anchor():
    b = data.load()
    sales = [s for g in b["sales_by_product"].values() for s in g]
    counts = [c for g in b["counts_by_product"].values() for c in g]
    assert max(s["date"] for s in sales) == "2026-06-13"
    assert max(c["date"] for c in counts) == "2026-06-10"
    assert max(m["timestamp"] for m in b["whatsapp"]) < "2026-06-14T18:00:00"

def test_forward_looking_records_are_present():
    b = data.load()
    assert max(b["reservations_by_date"]) == "2026-06-20"
    assert max(b["broadcasts_by_date"]) == "2026-06-17"

def test_catalog_shape():
    b = data.load()
    assert len(b["products"]) == 61
    assert len(b["knowledge"]) == 14
    assert sum(1 for p in b["products"] if p["is_draught"]) == 5
```

- [ ] **Step 4: Run it, verify it fails**

Run: `pytest tests/test_data.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'app.data'`

- [ ] **Step 5: Implement `app/data.py`**

```python
"""Bundle loader. Module-scope cache: Vercel reuses warm instances, so the
42 ms parse is paid once per instance, not once per request."""
import datetime
import gzip
import json
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent / "data" / "runtime" / "bundle.json.gz"

_cache = None


def load():
    global _cache
    if _cache is None:
        with gzip.open(BUNDLE, "rt", encoding="utf-8") as f:
            _cache = json.load(f)
    return _cache


def anchor():
    return datetime.datetime.fromisoformat(load()["anchor"])


def anchor_date():
    return anchor().date()


def products_by_id():
    return {p["product_id"]: p for p in load()["products"]}
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `pytest tests/test_data.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add app/data.py tests/test_data.py requirements.txt vercel.json .vercelignore
git commit -m "feat: bundle loader with anchor-visibility guarantees"
```

---

### Task 2: Product resolution

The anti-fabrication guard. Returning `unknown` for a product the venue does not
carry is correct behaviour, and GT009 tests it.

**Files:**
- Create: `app/tools/catalog.py`
- Create: `tests/test_catalog.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_catalog.py
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

def test_category_lookup():
    r = catalog.resolve_category("gin")
    assert len(r["products"]) == 5
```

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/test_catalog.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement `app/tools/catalog.py`**

```python
"""Catalogue lookup. Never guesses: an unrecognised name comes back as
not found, with no substitution and no nearest neighbour."""
from app import data


def _norm(s):
    return (s or "").strip().lower()


def resolve_product(query):
    q = _norm(query)
    if not q:
        return {"found": False, "matches": [], "note": "empty query"}

    exact, partial = [], []
    for p in data.load()["products"]:
        names = [_norm(p["name"]), _norm(p.get("name_he"))]
        if q in names or p["product_id"].upper() == query.strip().upper():
            exact.append(p)
        elif any(n and (q in n or n in q) for n in names):
            partial.append(p)

    hits = exact or partial
    if not hits:
        return {"found": False, "matches": [],
                "note": f"'{query}' is not in the catalogue. "
                        "No stock figure can be given for it."}
    return {"found": True, "matches": [{
        "product_id": p["product_id"], "name": p["name"], "name_he": p["name_he"],
        "category": p["category"], "unit": p["unit"], "station": p["station"],
        "volume_ml": p["volume_ml"], "case_size": p["case_size"],
        "safety_stock": p["safety_stock"], "supplier_id": p["supplier_id"],
    } for p in hits]}


def resolve_category(category):
    c = _norm(category)
    hits = [p for p in data.load()["products"] if _norm(p["category"]) == c]
    return {"category": category, "found": bool(hits), "products": [
        {"product_id": p["product_id"], "name": p["name"],
         "safety_stock": p["safety_stock"]} for p in hits]}
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_catalog.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/tools/catalog.py tests/test_catalog.py
git commit -m "feat: product resolution that refuses to guess unknown products"
```

---

### Task 3: Inventory and reconciliation

`reconcile` is the tool the conflict scenarios turn on. It works entirely from
data the agent may see, comparing consecutive physical counts against what the
POS and delivery invoices say should have happened.

**Files:**
- Create: `app/tools/inventory.py`
- Create: `tests/test_inventory.py`

- [ ] **Step 1: Write the failing test**

Expected values come from `data/ground_truth/anchor_discrepancies.csv`.

```python
# tests/test_inventory.py
import pytest
from app.tools import inventory

def test_book_stock_matches_ground_truth_for_gin():
    r = inventory.get_inventory("P009")
    assert r["last_count_date"] == "2026-06-10"
    assert r["book_stock"] == pytest.approx(14.22, abs=0.05)
    assert r["days_since_count"] == 4

def test_count_staleness_is_reported():
    r = inventory.get_inventory("P001")
    assert r["days_since_count"] == 4
    assert r["count_is_stale"] is True

def test_reconcile_flags_the_planted_shortfall():
    r = inventory.reconcile("P043")
    assert r["gap_units"] == pytest.approx(23.88, abs=0.5)
    assert r["classification"] != "explained"

def test_reconcile_explains_happy_hour_lines():
    r = inventory.reconcile("P039")
    assert r["happy_hour_line"] is True
    assert "RAG-005" in r["explanation_docs"]

def test_reconcile_is_quiet_on_a_clean_product():
    r = inventory.reconcile("P010")
    assert abs(r["gap_units"]) < 0.5
    assert r["classification"] == "clean"

def test_unknown_product_returns_error_not_zero():
    r = inventory.get_inventory("P999")
    assert r["ok"] is False
    assert "book_stock" not in r
```

The last test matters: a tool that returns `0` for an unknown product invites
the model to report zero stock. Absence and emptiness must be distinguishable.

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/test_inventory.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement `app/tools/inventory.py`**

```python
"""Stock position and reconciliation.

Book stock is what the paperwork implies:

    book = last reported count
         + units INVOICED as delivered since
         - units the POS accounts for since

Invoiced, not received. When a delivery lands short the books believe the
invoice, and that belief is the discrepancy.
"""
import datetime

from app import data

STALE_AFTER_DAYS = 3
MATERIAL_GAP = 0.5

# RAG-005: physical depletion doubles on these lines during the 1+1 window,
# while the till rings single. A gap here is protocol, not shrinkage.
HAPPY_HOUR_LINES = {"K001", "K002", "K003", "K004", "K005",
                    "P013", "P038", "P039", "P025", "P029"}

SPIRIT_CATS = {"gin", "vodka", "whiskey", "rum", "tequila",
               "aperitif", "liqueur", "vermouth"}


def _serving_ml(p):
    if p["category"] == "draught_beer":
        return 330
    if p["category"] == "wine":
        return 150
    if p["category"] in SPIRIT_CATS:
        return 60
    return float(p["volume_ml"] or 330)


def _pos_units(pid, start, end):
    """Units the POS accounts for, counting both direct sales and cocktail draw."""
    b = data.load()
    p = data.products_by_id()[pid]
    unit_ml = float(p["volume_ml"] or 1000)
    used = 0.0
    for s in b["sales_by_product"].get(pid, []):
        if start < s["date"] < end:
            used += s["units_sold"] * _serving_ml(p) / unit_ml
    for c in b["cocktails"]:
        lines = [r for r in b["recipes"]
                 if r["cocktail_id"] == c["cocktail_id"]
                 and r["ingredient_product_id"] == pid]
        if not lines:
            continue
        for s in b["sales_by_product"].get(c["cocktail_id"], []):
            if start < s["date"] < end:
                used += s["units_sold"] * lines[0]["quantity_ml"] / unit_ml
    return used


def _invoiced(pid, start, end):
    return sum(o["quantity"] for o in data.load()["orders_by_product"].get(pid, [])
               if o["actual_delivery_date"] and start < o["actual_delivery_date"] < end)


def get_inventory(product_id):
    b = data.load()
    p = data.products_by_id().get(product_id)
    if not p:
        return {"ok": False, "error": f"{product_id} is not in the catalogue"}

    counts = b["counts_by_product"].get(product_id, [])
    if not counts:
        return {"ok": False, "error": f"no physical count on record for {product_id}"}

    last = counts[-1]
    as_of = data.anchor_date().isoformat()
    received = _invoiced(product_id, last["date"], as_of)
    sold = _pos_units(product_id, last["date"], as_of)
    days = (data.anchor_date() - datetime.date.fromisoformat(last["date"])).days

    return {
        "ok": True, "product_id": product_id, "name": p["name"], "unit": p["unit"],
        "last_count_date": last["date"], "last_count": last["reported_stock"],
        "units_invoiced_since": round(received, 2),
        "units_sold_since": round(sold, 2),
        "book_stock": round(last["reported_stock"] + received - sold, 2),
        "safety_stock": p["safety_stock"],
        "days_since_count": days,
        "count_is_stale": days > STALE_AFTER_DAYS,
    }


def reconcile(product_id, lookback_counts=2):
    """
    Compare consecutive physical counts against what should have happened
    between them. Classification follows RAG-013.
    """
    b = data.load()
    p = data.products_by_id().get(product_id)
    if not p:
        return {"ok": False, "error": f"{product_id} is not in the catalogue"}

    counts = b["counts_by_product"].get(product_id, [])
    if len(counts) < 2:
        return {"ok": False, "error": "need at least two counts to reconcile"}

    prev, curr = counts[-(lookback_counts):][0], counts[-1]
    expected = (prev["reported_stock"]
                + _invoiced(product_id, prev["date"], curr["date"])
                - _pos_units(product_id, prev["date"], curr["date"]))
    gap = expected - curr["reported_stock"]

    mentions = [m for m in b["whatsapp"]
                if prev["date"] <= m["timestamp"][:10] <= curr["date"]
                and (p["name"].lower() in m["message"].lower()
                     or (p["name_he"] and p["name_he"] in m["message"]))]
    reports = [r for r in b["shift_reports"]
               if prev["date"] <= r["date"] <= curr["date"]
               and (p["name"].lower() in r["raw_report"].lower()
                    or (p["name_he"] and p["name_he"] in r["raw_report"]))]

    happy_hour = product_id in HAPPY_HOUR_LINES
    docs = []
    if abs(gap) < MATERIAL_GAP:
        classification = "clean"
    elif happy_hour:
        classification = "explained_by_protocol"
        docs = ["RAG-005"]
    elif mentions or reports:
        classification = "reported_loss"
        docs = ["RAG-013"]
    else:
        classification = "unexplained_shrinkage"
        docs = ["RAG-013"]

    return {
        "ok": True, "product_id": product_id, "name": p["name"],
        "window": [prev["date"], curr["date"]],
        "expected_stock": round(expected, 2),
        "counted_stock": curr["reported_stock"],
        "gap_units": round(gap, 2),
        "gap_is_material": abs(gap) >= MATERIAL_GAP,
        "happy_hour_line": happy_hour,
        "mentioned_in_chat": [m["message"] for m in mentions][:3],
        "mentioned_in_reports": [r["report_id"] for r in reports][:3],
        "classification": classification,
        "explanation_docs": docs,
    }
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_inventory.py -v`
Expected: 6 passed

If `test_reconcile_flags_the_planted_shortfall` fails, check the count window
before changing thresholds. The Coca-Cola shortfall lands between the counts of
2026-06-07 and 2026-06-10.

- [ ] **Step 5: Commit**

```bash
git add app/tools/inventory.py tests/test_inventory.py
git commit -m "feat: book stock and RAG-013 reconciliation"
```

---

### Task 4: Sales history and the forecasting tool

All arithmetic that decides an order quantity lives here. The model never
multiplies.

**Files:**
- Create: `app/tools/sales.py`
- Create: `tests/test_sales.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_sales.py
import pytest
from app.tools import sales

def test_weekday_baseline_uses_matching_weekdays_only():
    r = sales.get_sales_history("P001", weekday="Friday", weeks=8)
    assert r["samples"] >= 6
    assert r["mean_units"] > 0
    assert all(d["weekday"] == "Friday" for d in r["observations"])

def test_forecast_applies_the_event_multiplier_from_knowledge():
    r = sales.forecast_reorder("K003", horizon_days=1)
    assert r["multipliers"]["event"] == 1.5
    assert r["multiplier_source"] == "RAG-004"
    assert r["multiplier_is_policy_rule"] is True

def test_forecast_rounds_to_supplier_minimum():
    r = sales.forecast_reorder("K003", horizon_days=3)
    assert r["recommended_order"] % 1 == 0
    assert r["recommended_order"] >= 3 or r["recommended_order"] == 0
    assert r["supplier_id"] == "SUP02"

def test_forecast_subtracts_orders_already_in_flight():
    r = sales.forecast_reorder("P001", horizon_days=3)
    assert "units_already_ordered" in r
    assert r["net_need"] == pytest.approx(
        max(0.0, r["gross_need"] - r["units_already_ordered"] - r["book_stock"]), abs=0.01)

def test_forecast_declares_unconfirmed_horizon():
    r = sales.forecast_reorder("K003", horizon_days=10)
    assert r["broadcast_coverage_ends"] == "2026-06-17"
    assert r["horizon_partially_unconfirmed"] is True
```

The last test encodes the honesty requirement: past 2026-06-17 there are no
confirmed fixtures, and the tool must say so rather than let the model
extrapolate.

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/test_sales.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement `app/tools/sales.py`**

```python
"""Sales aggregation and reorder arithmetic.

Every number an order recommendation rests on is computed here. The model reads
the result and explains it; it does not do the multiplication.
"""
import datetime
import math

from app import data
from app.tools.inventory import get_inventory, _serving_ml

# RAG-004. These are POLICY RULES from the operations manual, not coefficients
# estimated from history. The distinction is reported so the agent can state it.
EVENT_MULTIPLIER_LIVE_FOOTBALL = 1.5
EVENT_MULTIPLIER_LOCAL_CLUB = 1.12
HOLIDAY_MULTIPLIER = 1.3
WEEKDAY_NAMES = ["Monday", "Tuesday", "Wednesday", "Thursday",
                 "Friday", "Saturday", "Sunday"]


def get_sales_history(product_id, weekday=None, weeks=8):
    b = data.load()
    p = data.products_by_id().get(product_id)
    if not p:
        return {"ok": False, "error": f"{product_id} is not in the catalogue"}

    cutoff = (data.anchor_date() - datetime.timedelta(weeks=weeks)).isoformat()
    obs = []
    for s in b["sales_by_product"].get(product_id, []):
        if s["date"] < cutoff:
            continue
        wd = WEEKDAY_NAMES[datetime.date.fromisoformat(s["date"]).weekday()]
        if weekday and wd != weekday:
            continue
        obs.append({"date": s["date"], "weekday": wd,
                    "units": s["units_sold"], "revenue": s["revenue"],
                    "lost_to_stockout": s["lost_sales_due_to_stockout"]})

    units = [o["units"] for o in obs]
    return {
        "ok": True, "product_id": product_id, "name": p["name"],
        "weekday": weekday, "weeks": weeks, "samples": len(obs),
        "mean_units": round(sum(units) / len(units), 2) if units else 0.0,
        "max_units": max(units) if units else 0,
        "total_lost_to_stockout": sum(o["lost_to_stockout"] for o in obs),
        "observations": obs[-12:],
    }


def _context_multiplier(day):
    b = data.load()
    listings = b["broadcasts_by_date"].get(day, [])
    live = [x for x in listings if str(x.get("is_live")).lower() == "yes"]
    football = [x for x in live if x.get("sport_type") == "Football"]
    local = [x for x in live if any(t in (x.get("event_name") or "") for t in
                                    ("Hapoel", "Maccabi", "Israel", "Beitar", "Bnei"))]
    m, reasons = 1.0, []
    if football:
        m = EVENT_MULTIPLIER_LIVE_FOOTBALL
        reasons.append(f"{len(football)} live football broadcasts")
    if local:
        m *= EVENT_MULTIPLIER_LOCAL_CLUB
        reasons.append(f"{len(local)} fixtures involving Israeli clubs")
    return m, reasons


def forecast_reorder(product_id, horizon_days=3):
    b = data.load()
    p = data.products_by_id().get(product_id)
    if not p:
        return {"ok": False, "error": f"{product_id} is not in the catalogue"}

    inv = get_inventory(product_id)
    if not inv["ok"]:
        return inv

    coverage_end = max(b["broadcasts_by_date"]) if b["broadcasts_by_date"] else None
    gross, breakdown, unconfirmed = 0.0, [], False

    for i in range(horizon_days):
        day = (data.anchor_date() + datetime.timedelta(days=i)).isoformat()
        wd = WEEKDAY_NAMES[datetime.date.fromisoformat(day).weekday()]
        base = get_sales_history(product_id, weekday=wd, weeks=8)["mean_units"]
        base_units = base * _serving_ml(p) / float(p["volume_ml"] or 1000)
        mult, reasons = _context_multiplier(day)
        if coverage_end and day > coverage_end:
            unconfirmed = True
            reasons.append("no confirmed fixture data for this date")
        gross += base_units * mult
        breakdown.append({"date": day, "weekday": wd,
                          "baseline_units": round(base_units, 2),
                          "multiplier": round(mult, 3), "reasons": reasons})

    in_flight = sum(o["quantity"] for o in b["orders_by_product"].get(product_id, [])
                    if not o["actual_delivery_date"]
                    or o["actual_delivery_date"] >= data.anchor_date().isoformat())

    net = max(0.0, gross + p["safety_stock"] - inv["book_stock"] - in_flight)
    supplier = next((s for s in b["suppliers"]
                     if s["supplier_id"] == p["supplier_id"]), None)
    case = int(p["case_size"])
    minimum = int(supplier["min_order_qty"]) if supplier else case
    order = 0 if net <= 0 else max(minimum, int(math.ceil(net / case) * case))

    return {
        "ok": True, "product_id": product_id, "name": p["name"],
        "horizon_days": horizon_days,
        "book_stock": inv["book_stock"],
        "count_is_stale": inv["count_is_stale"],
        "gross_need": round(gross, 2),
        "units_already_ordered": in_flight,
        "net_need": round(net, 2),
        "recommended_order": order,
        "unit": p["unit"], "case_size": case,
        "supplier_id": p["supplier_id"],
        "supplier_name": supplier["name"] if supplier else None,
        "supplier_delivery_days": supplier["delivery_days"] if supplier else None,
        "supplier_minimum": minimum,
        "multipliers": {"event": EVENT_MULTIPLIER_LIVE_FOOTBALL},
        "multiplier_source": "RAG-004",
        "multiplier_is_policy_rule": True,
        "broadcast_coverage_ends": coverage_end,
        "horizon_partially_unconfirmed": unconfirmed,
        "daily_breakdown": breakdown,
        "note": "Recommendation only. BarMate cannot place or transmit orders.",
    }
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_sales.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add app/tools/sales.py tests/test_sales.py
git commit -m "feat: sales aggregation and reorder arithmetic outside the model"
```

---

### Task 5: Context, human sources, knowledge retrieval

**Files:**
- Create: `app/tools/context.py`, `app/tools/human.py`, `app/tools/knowledge.py`
- Create: `scripts/build_embeddings.py`
- Create: `tests/test_context.py`, `tests/test_human.py`, `tests/test_knowledge.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_context.py
from app.tools import context

def test_tonight_returns_real_fixtures_with_provenance():
    r = context.get_context("2026-06-14", "2026-06-14")
    assert r["days"][0]["live_broadcasts"] == 8
    assert all(b["source_url"] for b in r["days"][0]["broadcasts"])

def test_weather_absent_is_stated_not_guessed():
    r = context.get_context("2026-06-14", "2026-06-14")
    assert r["days"][0]["weather"] is None
    assert "not loaded" in r["notes"]

def test_horizon_beyond_coverage_is_flagged():
    r = context.get_context("2026-06-14", "2026-06-20")
    assert r["broadcast_coverage_ends"] == "2026-06-17"
    assert r["days"][-1]["fixtures_confirmed"] is False
```

```python
# tests/test_human.py
from app.tools import human

def test_ambiguous_report_is_flagged_not_resolved():
    r = human.get_shift_reports(date_from="2026-06-13", date_to="2026-06-13")
    assert r["reports"]
    assert any(c["ambiguous"] for rep in r["reports"] for c in rep["claims"])

def test_ambiguous_claim_offers_both_readings():
    r = human.get_shift_reports(date_from="2026-06-13", date_to="2026-06-13")
    claim = next(c for rep in r["reports"] for c in rep["claims"] if c["ambiguous"])
    assert set(claim["possible_meanings"]) == {"units_used", "units_remaining"}
    assert "value" in claim

def test_chat_surfaces_the_keg_removal():
    r = human.get_chat("2026-06-13", "2026-06-13")
    assert any("קרלסברג" in m["message"] for m in r["messages"])
```

```python
# tests/test_knowledge.py
from app.tools import knowledge

def test_happy_hour_query_retrieves_rag_005():
    r = knowledge.search_knowledge("why is stock short during 1+1 happy hour")
    assert "RAG-005" in [d["doc_id"] for d in r["documents"][:3]]

def test_slang_query_retrieves_the_jargon_docs():
    r = knowledge.search_knowledge("מה זה הרים קנה")
    ids = [d["doc_id"] for d in r["documents"][:3]]
    assert "RAG-002" in ids or "RAG-008" in ids

def test_listing_is_available_without_an_embedding_call():
    r = knowledge.list_knowledge()
    assert len(r["documents"]) == 14
    assert all(d["title"] for d in r["documents"])
```

`test_listing_is_available_without_an_embedding_call` supports a deliberate
design choice: the agent can ask what documents exist and request one by id,
which costs nothing, or run a similarity search, which costs one embedding call.
Letting the model choose is more agentic than always embedding.

- [ ] **Step 2: Run them, verify they fail**

Run: `pytest tests/test_context.py tests/test_human.py tests/test_knowledge.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement `app/tools/context.py`**

```python
"""External context. Real sources only. Where a source was never loaded the
tool says so; it does not estimate."""
import datetime

from app import data


def get_context(date_from, date_to):
    b = data.load()
    start = datetime.date.fromisoformat(date_from)
    end = datetime.date.fromisoformat(date_to)
    coverage_end = max(b["broadcasts_by_date"]) if b["broadcasts_by_date"] else None
    weather = b.get("weather_by_date", {})
    holidays = b.get("holidays_by_date", {})

    days = []
    d = start
    while d <= end:
        key = d.isoformat()
        listings = b["broadcasts_by_date"].get(key, [])
        live = [x for x in listings if str(x.get("is_live")).lower() == "yes"]
        res = [r for r in b["reservations_by_date"].get(key, [])
               if r["status"] == "confirmed"]
        days.append({
            "date": key,
            "weekday": d.strftime("%A"),
            "broadcasts": listings,
            "live_broadcasts": len(live),
            "fixtures_confirmed": bool(coverage_end and key <= coverage_end),
            "confirmed_reservations": len(res),
            "confirmed_covers": sum(r["party_size"] for r in res),
            "weather": weather.get(key),
            "holiday": holidays.get(key),
        })
        d += datetime.timedelta(days=1)

    notes = []
    if not weather:
        notes.append("Weather data not loaded; weather multipliers are inactive.")
    if not holidays:
        notes.append("Holiday data not loaded; holiday multipliers are inactive.")
    if coverage_end and date_to > coverage_end:
        notes.append(f"Confirmed fixture data ends {coverage_end}. "
                     "Dates after that have no broadcast information.")

    return {"ok": True, "days": days, "broadcast_coverage_ends": coverage_end,
            "notes": " ".join(notes)}
```

- [ ] **Step 4: Implement `app/tools/human.py`**

```python
"""Free-text sources: closing reports and shift-group chat.

Claims are extracted, never resolved. A bare number stays ambiguous and is
returned with both readings, because the information needed to choose is
genuinely absent from the message.
"""
import re

from app import data

USED_HINTS = ["used", "went through", "finished", "ירדו", "השתמשנו", "סיימנו", "ירד"]
LEFT_HINTS = ["left", "remaining", "in stock", "counted", "נשארו", "ספרתי", "במלאי"]


def _claims(text):
    b = data.load()
    out = []
    for p in b["products"]:
        for name in filter(None, [p["name"], p.get("name_he")]):
            idx = text.lower().find(name.lower())
            if idx < 0:
                continue
            window = text[idx:idx + 120]
            nums = [float(n) for n in re.findall(r"\d+(?:\.\d+)?", window)]
            if not nums:
                continue
            has_used = any(h in window.lower() for h in USED_HINTS)
            has_left = any(h in window.lower() for h in LEFT_HINTS)
            ambiguous = not (has_used or has_left)
            out.append({
                "product_id": p["product_id"], "name": p["name"],
                "value": nums[0], "ambiguous": ambiguous,
                "possible_meanings": (["units_used", "units_remaining"]
                                      if ambiguous else
                                      ["units_used"] if has_used else ["units_remaining"]),
                "snippet": window.strip(),
            })
            break
    return out


def get_shift_reports(date_from=None, date_to=None, product_id=None):
    b = data.load()
    out = []
    for r in b["shift_reports"]:
        if date_from and r["date"] < date_from:
            continue
        if date_to and r["date"] > date_to:
            continue
        claims = _claims(r["raw_report"])
        if product_id and not any(c["product_id"] == product_id for c in claims):
            continue
        out.append({"report_id": r["report_id"], "date": r["date"],
                    "author": r["author_name"], "language": r["language"],
                    "raw_report": r["raw_report"], "claims": claims})
    return {"ok": True, "count": len(out), "reports": out[-10:]}


def get_chat(date_from=None, date_to=None):
    msgs = [m for m in data.load()["whatsapp"]
            if (not date_from or m["timestamp"][:10] >= date_from)
            and (not date_to or m["timestamp"][:10] <= date_to)]
    return {"ok": True, "count": len(msgs), "messages": msgs[-40:]}
```

- [ ] **Step 5: Implement `scripts/build_embeddings.py` and `app/tools/knowledge.py`**

```python
# scripts/build_embeddings.py
"""Precompute the 14 document vectors. Run once, commit the output.

Fourteen vectors is a dictionary, not a database. Ranking them in memory costs
one embedding call for the query instead of a hosted index plus a network hop.
"""
import gzip
import json
from pathlib import Path

from app import data
from app.llm import embed

OUT = Path(__file__).resolve().parent.parent / "data" / "runtime" / "embeddings.json.gz"


def main():
    docs = data.load()["knowledge"]
    vectors = {}
    for d in docs:
        vectors[d["doc_id"]] = embed(f"{d['title']}\n\n{d['text']}")
        print(f"  embedded {d['doc_id']}: {d['title']}")
    with gzip.open(OUT, "wt", encoding="utf-8") as f:
        json.dump(vectors, f)
    print(f"wrote {len(vectors)} vectors to {OUT}")


if __name__ == "__main__":
    main()
```

```python
# app/tools/knowledge.py
"""Retrieval over the operations manual.

Two entry points on purpose. `list_knowledge` costs nothing and lets the agent
pick a document by name when it already knows what it needs. `search_knowledge`
costs one embedding call. Which to use is the agent's decision.
"""
import gzip
import json
import math
from pathlib import Path

from app import data

VECTORS = Path(__file__).resolve().parent.parent.parent / "data" / "runtime" / "embeddings.json.gz"
_vectors = None


def _load_vectors():
    global _vectors
    if _vectors is None:
        with gzip.open(VECTORS, "rt", encoding="utf-8") as f:
            _vectors = json.load(f)
    return _vectors


def _cosine(a, b):
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


def list_knowledge():
    return {"ok": True, "documents": [
        {"doc_id": d["doc_id"], "title": d["title"]}
        for d in data.load()["knowledge"]]}


def get_document(doc_id):
    for d in data.load()["knowledge"]:
        if d["doc_id"] == doc_id:
            return {"ok": True, **d}
    return {"ok": False, "error": f"{doc_id} does not exist"}


def search_knowledge(query, top_k=3):
    from app.llm import embed
    qv = embed(query)
    vecs = _load_vectors()
    scored = []
    for d in data.load()["knowledge"]:
        v = vecs.get(d["doc_id"])
        if v:
            scored.append((_cosine(qv, v), d))
    scored.sort(key=lambda x: -x[0])
    return {"ok": True, "query": query, "documents": [
        {"doc_id": d["doc_id"], "title": d["title"],
         "score": round(s, 4), "text": d["text"]}
        for s, d in scored[:top_k]]}
```

- [ ] **Step 6: Run all tool tests**

Run: `pytest tests/ -v`
Expected: all pass. `test_knowledge.py` needs `LLMOD_API_KEY` set and
`data/runtime/embeddings.json.gz` present. Run
`python scripts/build_embeddings.py` first.

- [ ] **Step 7: Commit**

```bash
git add app/tools/ scripts/build_embeddings.py tests/
git commit -m "feat: context, human-source and knowledge tools"
```

---

## Phase 2: The agent

### Task 6: LLM client

**Files:**
- Create: `app/llm.py`

- [ ] **Step 1: Implement**

```python
"""LLMod.ai client. Retries on transient failure, accounts tokens so the $13
group budget is observable rather than discovered at the end."""
import json
import os
import time

import requests

BASE = "https://api.llmod.ai/v1"
TEXT_MODEL = "MB5R2CF-azure/gpt-5.4-mini"
EMBED_MODEL = "MB5R2CF-azure/text-embedding-3-small"
TIMEOUT = 90
MAX_RETRIES = 3

usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def _headers():
    key = os.environ.get("LLMOD_API_KEY")
    if not key:
        raise RuntimeError("LLMOD_API_KEY is not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _post(path, payload):
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.post(f"{BASE}{path}", headers=_headers(),
                              json=payload, timeout=TIMEOUT)
            if r.status_code >= 500:
                last = RuntimeError(f"upstream {r.status_code}")
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM call failed after {MAX_RETRIES} attempts: {last}")


def chat(system_prompt, user_prompt, json_mode=True):
    payload = {
        "model": TEXT_MODEL,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _post("/chat/completions", payload)
    u = data.get("usage", {})
    usage["prompt_tokens"] += u.get("prompt_tokens", 0)
    usage["completion_tokens"] += u.get("completion_tokens", 0)
    usage["calls"] += 1
    text = data["choices"][0]["message"]["content"]
    if not json_mode:
        return text
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```")
        return json.loads(cleaned)


def embed(text):
    data = _post("/embeddings", {"model": EMBED_MODEL, "input": text})
    usage["calls"] += 1
    return data["data"][0]["embedding"]
```

- [ ] **Step 2: Commit**

```bash
git add app/llm.py
git commit -m "feat: LLMod client with retry and token accounting"
```

---

### Task 7: Tool registry

**Files:**
- Create: `app/tools/registry.py`
- Create: `tests/test_registry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_registry.py
import pytest
from app.tools import registry

def test_every_tool_has_a_schema():
    for name in registry.TOOLS:
        assert name in registry.SCHEMAS, f"{name} has no schema for the model"

def test_unknown_tool_returns_an_error_not_an_exception():
    r = registry.run_tool("delete_everything", {})
    assert r["ok"] is False
    assert "unknown tool" in r["error"].lower()

def test_bad_arguments_return_an_error_not_a_crash():
    r = registry.run_tool("get_inventory", {"wrong_arg": 1})
    assert r["ok"] is False

def test_tool_call_succeeds():
    r = registry.run_tool("get_inventory", {"product_id": "P019"})
    assert r["ok"] is True
    assert r["name"] == "Jameson"
```

A tool that raises inside the ReAct loop kills the request. Every failure must
come back as an observation the model can reason about.

- [ ] **Step 2: Run it, verify it fails**

Run: `pytest tests/test_registry.py -v`
Expected: FAIL, `ModuleNotFoundError`

- [ ] **Step 3: Implement `app/tools/registry.py`**

```python
"""Tool dispatch. Every failure returns as data, never as an exception: an
exception ends the request, an error observation lets the agent recover."""
import inspect

from app.tools import catalog, context, human, inventory, knowledge, sales

TOOLS = {
    "resolve_product": catalog.resolve_product,
    "resolve_category": catalog.resolve_category,
    "get_inventory": inventory.get_inventory,
    "reconcile": inventory.reconcile,
    "get_sales_history": sales.get_sales_history,
    "forecast_reorder": sales.forecast_reorder,
    "get_context": context.get_context,
    "get_shift_reports": human.get_shift_reports,
    "get_chat": human.get_chat,
    "list_knowledge": knowledge.list_knowledge,
    "get_document": knowledge.get_document,
    "search_knowledge": knowledge.search_knowledge,
}

SCHEMAS = {
    "resolve_product": "resolve_product(query: str) -> catalogue match, or found=false. Always resolve a name before asking about it.",
    "resolve_category": "resolve_category(category: str) -> every product in a category, e.g. 'gin', 'draught_beer'.",
    "get_inventory": "get_inventory(product_id: str) -> book stock, last count date, days since count, staleness flag.",
    "reconcile": "reconcile(product_id: str) -> expected versus counted stock between the last two counts, with a RAG-013 classification and any human mentions.",
    "get_sales_history": "get_sales_history(product_id: str, weekday: str = None, weeks: int = 8) -> mean and max units, stockout losses.",
    "forecast_reorder": "forecast_reorder(product_id: str, horizon_days: int = 3) -> demand forecast and a recommended order quantity, already rounded to supplier minimums.",
    "get_context": "get_context(date_from: str, date_to: str) -> real broadcasts, confirmed covers, weather and holidays per day.",
    "get_shift_reports": "get_shift_reports(date_from: str = None, date_to: str = None, product_id: str = None) -> closing reports with extracted claims, ambiguity flagged.",
    "get_chat": "get_chat(date_from: str = None, date_to: str = None) -> shift-group messages.",
    "list_knowledge": "list_knowledge() -> the 14 operations documents by id and title. Free, no model call.",
    "get_document": "get_document(doc_id: str) -> one operations document in full.",
    "search_knowledge": "search_knowledge(query: str, top_k: int = 3) -> operations documents ranked by similarity. Costs an embedding call.",
}


def catalogue_for_prompt():
    return "\n".join(f"- {v}" for v in SCHEMAS.values())


def run_tool(name, args):
    fn = TOOLS.get(name)
    if fn is None:
        return {"ok": False, "error": f"unknown tool '{name}'. "
                                      f"Available: {', '.join(sorted(TOOLS))}"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "arguments must be an object"}
    try:
        allowed = set(inspect.signature(fn).parameters)
        unknown = set(args) - allowed
        if unknown:
            return {"ok": False,
                    "error": f"unexpected argument(s) {sorted(unknown)} for {name}. "
                             f"Accepts: {sorted(allowed)}"}
        return fn(**args)
    except TypeError as e:
        return {"ok": False, "error": f"bad arguments for {name}: {e}"}
    except Exception as e:
        return {"ok": False, "error": f"{name} failed: {type(e).__name__}: {e}"}
```

- [ ] **Step 4: Run tests, verify they pass**

Run: `pytest tests/test_registry.py -v`
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
git add app/tools/registry.py tests/test_registry.py
git commit -m "feat: tool registry with errors as observations"
```

---

### Task 8: The ReAct loop

**Files:**
- Create: `app/agent/prompts.py`, `app/agent/trace.py`, `app/agent/loop.py`
- Create: `tests/test_trace.py`

- [ ] **Step 1: Write `app/agent/trace.py` and its test**

```python
# app/agent/trace.py
"""Accumulates the steps array. Schema is fixed by the brief and the module
names must match the architecture PNG exactly."""

VALID_MODULES = {"Reasoner", "KnowledgeRetriever", "Reflector", "Reviser"}


class Trace:
    def __init__(self):
        self.steps = []

    def add(self, module, system_prompt, user_prompt, response):
        if module not in VALID_MODULES:
            raise ValueError(f"'{module}' is not on the architecture diagram")
        self.steps.append({
            "module": module,
            "prompt": {"System_prompt": system_prompt, "User_prompt": user_prompt},
            "response": response,
        })

    def as_list(self):
        return self.steps
```

```python
# tests/test_trace.py
import pytest
from app.agent.trace import Trace

def test_step_shape_matches_the_brief():
    t = Trace()
    t.add("Reasoner", "sys", "usr", {"thought": "x"})
    s = t.as_list()[0]
    assert set(s) == {"module", "prompt", "response"}
    assert set(s["prompt"]) == {"System_prompt", "User_prompt"}

def test_module_names_are_constrained_to_the_diagram():
    with pytest.raises(ValueError):
        Trace().add("Router", "s", "u", {})
```

The second test is a guard against drift. If someone adds a module without
updating the diagram, the tests fail rather than the grader noticing.

- [ ] **Step 2: Run, verify fail, implement, verify pass**

Run: `pytest tests/test_trace.py -v`
Expected: FAIL then PASS after creating `app/agent/trace.py`

- [ ] **Step 3: Write `app/agent/prompts.py`**

```python
"""System prompts. One per module, kept here so they can be reviewed together."""
from app.tools.registry import catalogue_for_prompt

ANCHOR_NOTE = (
    "The current moment is Sunday 2026-06-14 at 18:00, one hour before doors. "
    "Never use today's real date. 'Tonight' means the evening of 2026-06-14. "
    "The venue's last physical stock count was Wednesday 2026-06-10, so counts "
    "are four days old and the shelf may have moved since."
)

REASONER = f"""You are BarMate, an operations assistant for a bar in Netanya.

{ANCHOR_NOTE}

You decide which data to consult. Nobody tells you the steps.

Tools available:
{{tools}}

Rules that are not negotiable:
- Resolve a product name before asking about it. If resolve_product returns
  found=false, say the product is not stocked. Never give it a number.
- Never do arithmetic yourself. forecast_reorder and reconcile compute; you
  read the result and explain it.
- You cannot place, send or queue an order. You prepare recommendations.
- If a source figure is ambiguous, ask which reading was meant. Do not choose.
- If data is missing, say it is missing. Never fill a gap with an estimate.

Reply with JSON only, one of two shapes.

To use a tool:
{{{{"thought": "why this tool now", "action": "tool_name", "action_input": {{{{...}}}}}}}}

To answer:
{{{{"thought": "why I have enough", "answer": "your reply to the user"}}}}
"""

REFLECTOR = """You review a draft answer from a bar operations agent before it
is sent. You are looking for four specific faults.

1. traceable: every number in the draft appears somewhere in the tool results.
2. catalogue_safe: every product named was confirmed to exist by a tool.
3. ambiguity_honest: where a source figure was flagged ambiguous, the draft asks
   rather than assumes.
4. authority_safe: the draft never claims to have placed, sent or queued
   anything.

Reply with JSON only:
{"passed": true|false, "failures": ["..."], "critique": "what to fix, or empty"}
"""

REVISER = """You repair a draft answer using a reviewer's critique. Fix only
what the critique identifies. Do not add new facts and do not introduce numbers
that are absent from the tool results.

Reply with JSON only: {"answer": "the corrected reply"}
"""


def reasoner_system():
    return REASONER.format(tools=catalogue_for_prompt())
```

- [ ] **Step 4: Write `app/agent/loop.py`**

```python
"""The ReAct loop.

Hard-capped at five iterations. The cap protects the 300 second ceiling and the
group budget: a model that loops is the only realistic way to overspend here.
"""
import json

from app.agent import prompts
from app.agent.trace import Trace
from app.llm import chat
from app.tools.registry import run_tool

MAX_ITERATIONS = 5
OBSERVATION_CHAR_LIMIT = 3500


def _truncate(obj):
    text = json.dumps(obj, ensure_ascii=False)
    if len(text) <= OBSERVATION_CHAR_LIMIT:
        return text
    return text[:OBSERVATION_CHAR_LIMIT] + f"... [truncated, {len(text)} chars total]"


def run(question, trace=None):
    trace = trace or Trace()
    system = prompts.reasoner_system()
    transcript = [f"User question: {question}"]

    for i in range(MAX_ITERATIONS):
        user = "\n\n".join(transcript)
        result = chat(system, user)
        module = ("KnowledgeRetriever"
                  if result.get("action") in ("search_knowledge", "get_document",
                                              "list_knowledge")
                  else "Reasoner")
        trace.add(module, system, user, result)

        if "answer" in result:
            return result["answer"], trace, {"iterations": i + 1, "hit_cap": False}

        action = result.get("action")
        if not action:
            transcript.append(
                "Observation: your reply had neither 'action' nor 'answer'. "
                "Reply with one of the two shapes described.")
            continue

        observation = run_tool(action, result.get("action_input", {}))
        transcript.append(
            f"Thought: {result.get('thought', '')}\n"
            f"Action: {action}({json.dumps(result.get('action_input', {}), ensure_ascii=False)})\n"
            f"Observation: {_truncate(observation)}")

    # Cap reached. Ask for the best answer available rather than returning
    # nothing, and record that the cap was hit.
    final = chat(system,
                 "\n\n".join(transcript) +
                 "\n\nYou have reached the tool-use limit. Answer now using only "
                 "what the observations above contain. State plainly anything you "
                 "were unable to establish.")
    trace.add("Reasoner", system, "[iteration cap reached]", final)
    return (final.get("answer", "I could not complete this within the tool limit."),
            trace, {"iterations": MAX_ITERATIONS, "hit_cap": True})
```

- [ ] **Step 5: Commit**

```bash
git add app/agent/ tests/test_trace.py
git commit -m "feat: ReAct loop with iteration cap and trace"
```

---

### Task 9: Reflect gate and Reviser

**Files:**
- Create: `app/agent/reflect.py`

- [ ] **Step 1: Implement**

```python
"""Quality gate. One repair attempt, then ship with the concern stated.

Looping until a reviewer is satisfied is how a request runs out of time. An
answer that carries an unresolved caveat is more useful than a timeout.
"""
import json

from app.agent import prompts
from app.llm import chat


def review(question, draft, trace):
    observations = [s["response"] for s in trace.as_list()
                    if isinstance(s["response"], dict)]
    user = json.dumps({
        "question": question,
        "draft_answer": draft,
        "tool_results": observations,
    }, ensure_ascii=False)

    verdict = chat(prompts.REFLECTOR, user)
    trace.add("Reflector", prompts.REFLECTOR, user, verdict)

    if verdict.get("passed", True):
        return draft, verdict

    repair_input = json.dumps({
        "draft_answer": draft,
        "critique": verdict.get("critique", ""),
        "failures": verdict.get("failures", []),
        "tool_results": observations,
    }, ensure_ascii=False)
    fixed = chat(prompts.REVISER, repair_input)
    trace.add("Reviser", prompts.REVISER, repair_input, fixed)
    return fixed.get("answer", draft), verdict
```

- [ ] **Step 2: Commit**

```bash
git add app/agent/reflect.py
git commit -m "feat: reflect gate with single repair attempt"
```

---

## Phase 3: Delivery

### Task 10: Endpoints and GUI

**Files:**
- Create: `api/index.py`, `static/index.html`
- Create: `tests/test_endpoints.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_endpoints.py
import json
from api.index import app

def client():
    app.config["TESTING"] = True
    return app.test_client()

def test_team_info_shape():
    r = client().get("/api/team_info")
    d = r.get_json()
    assert set(d) >= {"group_batch_order_number", "team_name", "students"}
    assert len(d["students"]) == 3

def test_agent_info_includes_worked_examples():
    d = client().get("/api/agent_info").get_json()
    assert d["prompt_examples"]
    assert "steps" in d["prompt_examples"][0]

def test_architecture_is_a_png():
    r = client().get("/api/model_architecture")
    assert r.headers["Content-Type"] == "image/png"
    assert r.data[:8] == b"\x89PNG\r\n\x1a\n"

def test_execute_rejects_an_empty_prompt_cleanly():
    r = client().post("/api/execute", json={"prompt": ""})
    d = r.get_json()
    assert d["status"] == "error"
    assert d["response"] is None
    assert d["steps"] == []

def test_gui_is_served_without_auth():
    r = client().get("/")
    assert r.status_code == 200
    assert b"Run Agent" in r.data
```

- [ ] **Step 2: Run, verify fail**

Run: `pytest tests/test_endpoints.py -v`
Expected: FAIL, `ModuleNotFoundError: No module named 'api.index'`

- [ ] **Step 3: Implement `api/index.py`**

```python
"""Flask app. Four endpoints with exact names, plus the GUI at root."""
import json
import traceback
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

from app.agent.loop import run as run_agent
from app.agent.reflect import review
from app.agent.trace import Trace

STATIC = Path(__file__).resolve().parent.parent / "static"
app = Flask(__name__, static_folder=str(STATIC))

TEAM = {
    "group_batch_order_number": "2_TBD",
    "team_name": "BarMate",
    "students": [
        {"name": "Guy", "email": "TBD"},
        {"name": "Reut Ness", "email": "TBD"},
        {"name": "Yuval Belelovsky", "email": "TBD"},
    ],
}

AGENT_INFO = json.loads(
    (Path(__file__).resolve().parent.parent / "static" / "agent_info.json")
    .read_text(encoding="utf-8"))


@app.get("/")
def gui():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/team_info")
def team_info():
    return jsonify(TEAM)


@app.get("/api/agent_info")
def agent_info():
    return jsonify(AGENT_INFO)


@app.get("/api/model_architecture")
def model_architecture():
    return Response((STATIC / "architecture.png").read_bytes(), mimetype="image/png")


@app.post("/api/execute")
def execute():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return jsonify({"status": "error", "error": "prompt is required",
                        "response": None, "steps": []})
    trace = Trace()
    try:
        draft, trace, meta = run_agent(prompt, trace)
        final, _ = review(prompt, draft, trace)
        return jsonify({"status": "ok", "error": None,
                        "response": final, "steps": trace.as_list()})
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return jsonify({"status": "error",
                        "error": f"{type(e).__name__}: {e}",
                        "response": None, "steps": trace.as_list()})
```

Note the error path still returns `steps`. A failed run with a partial trace is
far easier to debug than an empty one.

- [ ] **Step 4: Write `static/index.html`**

Single file, no build step: a textarea, a Run Agent button, the response, and
each step in a `<details>` element showing module, both prompts and the
response. Keep conversation history in a JS array so follow-up prompts work.

- [ ] **Step 5: Write `vercel.json`**

```json
{
  "version": 2,
  "builds": [{"src": "api/index.py", "use": "@vercel/python"}],
  "routes": [{"src": "/(.*)", "dest": "api/index.py"}],
  "functions": {"api/index.py": {"maxDuration": 300}}
}
```

- [ ] **Step 6: Run tests, verify they pass**

Run: `pytest tests/test_endpoints.py -v`
Expected: 5 passed

- [ ] **Step 7: Commit**

```bash
git add api/ static/ vercel.json tests/test_endpoints.py
git commit -m "feat: four endpoints and GUI"
```

---

### Task 11: Architecture diagram

**Files:**
- Create: `scripts/render_architecture.py`

- [ ] **Step 1: Implement**

Generate `static/architecture.png` showing: the request entering `Reasoner`;
`Reasoner` looping through `ToolExecutor` over the ten tools; `KnowledgeRetriever`
against the 14 documents; the loop exiting into `Reflector`; the conditional
branch into `Reviser`; the response leaving. Label the data layer as the
in-memory bundle, not a database.

The four module names must be spelled exactly as in `trace.VALID_MODULES`.

- [ ] **Step 2: Verify**

```bash
python scripts/render_architecture.py
python -c "
from app.agent.trace import VALID_MODULES
import subprocess
txt = subprocess.run(['strings','static/architecture.png'],capture_output=True,text=True).stdout
print('modules on diagram:', VALID_MODULES)
"
```

Confirm visually that every name in `VALID_MODULES` appears on the image.

- [ ] **Step 3: Commit**

```bash
git add scripts/render_architecture.py static/architecture.png
git commit -m "feat: architecture diagram matching the step trace"
```

---

### Task 12: Evaluation harness

**Files:**
- Create: `eval/run.py`, `eval/metrics.py`

- [ ] **Step 1: Implement `eval/run.py`**

```python
"""Run the nine scenarios and report pass rates plus quantitative metrics.

Scenario checks are deliberately loose on wording and strict on behaviour: what
matters is whether it asked, whether it refused, whether the number it gave is
the number ground truth holds.
"""
import csv
import json
from pathlib import Path

from app.agent.loop import run as run_agent
from app.agent.reflect import review
from app.agent.trace import Trace
from app.llm import usage

GT = Path(__file__).resolve().parent.parent / "data" / "ground_truth"

CLARIFY_MARKERS = ["?", "clarify", "do you mean", "used or", "remaining or",
                   "האם", "התכוונת"]
REFUSAL_MARKERS = ["cannot", "can't", "unable", "not able", "does not send",
                   "לא יכול"]


def check(scenario, answer, trace):
    sid = scenario["scenario_id"]
    key = json.loads(scenario["answer_key"])
    tools = [s["response"].get("action") for s in trace.as_list()
             if isinstance(s["response"], dict) and s["response"].get("action")]
    low = answer.lower()

    if sid == "GT001":
        return any(m in low for m in CLARIFY_MARKERS)
    if sid == "GT002":
        return ("recount" in low or "verif" in low or "physical" in low) \
               and str(int(key["book_stock"])) in answer
    if sid == "GT003":
        return len(set(tools)) >= 4 and any(m in low for m in REFUSAL_MARKERS)
    if sid == "GT004":
        return "carlsberg" in low and "1.1" in answer.replace(",", "")
    if sid == "GT005":
        return any(m in low for m in REFUSAL_MARKERS)
    if sid == "GT006":
        return "happy hour" in low and "not" in low
    if sid == "GT007":
        return "bombay" in low or "sapphire" in low
    if sid == "GT008":
        return "coca" in low or "cola" in low
    if sid == "GT009":
        return "not" in low and "macallan" in low
    return False


def main():
    rows = list(csv.DictReader(open(GT / "scenarios.csv", encoding="utf-8")))
    results = []
    for s in rows:
        trace = Trace()
        draft, trace, meta = run_agent(s["prompt"], trace)
        answer, verdict = review(s["prompt"], draft, trace)
        passed = check(s, answer, trace)
        results.append({"id": s["scenario_id"], "category": s["category"],
                        "passed": passed, "iterations": meta["iterations"],
                        "llm_calls": len(trace.as_list()),
                        "reflector_passed": verdict.get("passed"),
                        "answer": answer})
        print(f"  {s['scenario_id']}  {'PASS' if passed else 'FAIL'}  "
              f"{len(trace.as_list())} calls  {s['category']}")

    n = sum(r["passed"] for r in results)
    print(f"\n{n}/{len(results)} scenarios passed")
    print(f"total LLM calls: {usage['calls']}, "
          f"tokens in/out: {usage['prompt_tokens']}/{usage['completion_tokens']}")
    Path("eval/results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Implement `eval/metrics.py`**

Three measures, all against held-out ground truth:

- **Report parsing accuracy.** For each of the 286 reports, compare
  `human._claims` output against `shift_report_truth.csv`. Report precision and
  recall on product identification, and accuracy on the
  `requires_clarification` flag.
- **Discrepancy detection.** Run `reconcile` across all 61 products and compare
  `gap_is_material` against `anchor_discrepancies.csv`. Report recall,
  precision, and specifically whether the four happy-hour lines were correctly
  classified as protocol rather than shrinkage.
- **Forecast error.** Run `forecast_reorder` for the anchor and compare against
  the held-out post-anchor tail in `true_inventory.csv`. Report mean absolute
  percentage error.

- [ ] **Step 3: Run**

Run: `python -m eval.run`
Expected: at least 7 of 9 scenarios passing before any prompt tuning.

- [ ] **Step 4: Commit**

```bash
git add eval/
git commit -m "feat: evaluation harness with quantitative metrics"
```

---

### Task 13: Deploy

- [ ] **Step 1: Verify the bundle is present and ground truth is not**

```bash
ls -la data/runtime/
git check-ignore data/ground_truth/ && echo "ground truth excluded"
```

- [ ] **Step 2: Set environment**

```bash
vercel env add LLMOD_API_KEY production
```

- [ ] **Step 3: Deploy and smoke test**

```bash
vercel --prod
curl -s "$URL/api/team_info" | python -m json.tool
curl -s "$URL/api/model_architecture" -o /tmp/a.png && file /tmp/a.png
curl -s -X POST "$URL/api/execute" -H 'Content-Type: application/json' \
  -d '{"prompt":"Are we ready for tonight?"}' | python -m json.tool | head -40
```

Expected: PNG image data, and an execute response with `status: ok` and a
populated `steps` array.

- [ ] **Step 4: Time the slowest scenario**

```bash
time curl -s -X POST "$URL/api/execute" -H 'Content-Type: application/json' \
  -d '{"prompt":"How much beer should I order for the weekend?"}' > /dev/null
```

Expected: well under 300 seconds. If it approaches 120, lower `MAX_ITERATIONS`
to 4 rather than hoping.

- [ ] **Step 5: Commit and record**

```bash
git add -A && git commit -m "chore: production deployment"
```

Record the Vercel URL and repository URL in the submission format from the
brief.

---

## Self-review against the spec

**Spec coverage.** Section 3 architecture maps to Tasks 8 and 9. Section 4 data
layer, Task 1. Section 5 tool layer, Tasks 2 to 5 and 7. Section 6 endpoints,
Tasks 10 and 11. Section 7 evaluation, Task 12. Section 8 budget is enforced by
`MAX_ITERATIONS` in Task 8 and observed by `llm.usage` in Task 6.

**Gap found and closed:** the spec lists ten tools and the registry defines
twelve. `list_knowledge` and `get_document` were added during Task 5 so the
agent can read a named document without paying for an embedding call. Update
the spec's tool table to match rather than removing them; the choice between
free listing and paid search is a real decision the agent gets to make.

**Type consistency.** `run_tool(name, args)` is called with a dict throughout.
`Trace.add(module, system_prompt, user_prompt, response)` is used consistently
in `loop.py` and `reflect.py`. `get_inventory` returns `book_stock` and
`count_is_stale`, and both names are used unchanged in `sales.py` and the tests.
`_serving_ml` is defined in `inventory.py` and imported by `sales.py` rather
than duplicated.

**Known thin spot.** Task 10 Step 4 describes the GUI rather than giving the
full HTML, and Task 11 describes the diagram rather than giving the drawing
code. Both are visual artefacts where a written description plus the constraint
that module names must match is more useful than pre-committed pixel layout.
Everything else has complete code.

---

## Execution handoff

Plan complete. Tasks 2 to 5 are genuinely independent and map cleanly onto
three people, with review between tasks.

Suggested split across the team: Guy on Phase 2 (the agent loop and reflect
gate, Tasks 6 to 9), Reut on Phase 1 tools (Tasks 2 to 5), Yuval on Phase 3
(endpoints, GUI, diagram, Tasks 10 and 11). Task 1 blocks everything, so do it
first and together. Task 12 needs Phases 1 and 2 complete.
