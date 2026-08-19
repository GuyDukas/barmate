"""
Validation harness.

Recomputes book stock exactly the way the agent's `reconcile` tool will have to:

    book = last reported count
         + units received since that count
         - units the POS accounts for since that count

and compares it against physical truth. A gap means stock moved without the
books knowing, which is what BarMate exists to surface.
"""
from collections import defaultdict

from .engine import serving_ml


def book_stock(sim, pid, as_of):
    counts = [c for c in sim.counts if c["product_id"] == pid and c["date"] < as_of]
    if not counts:
        return None
    last = max(counts, key=lambda c: c["date"])
    p = sim.by_id[pid]
    unit_ml = float(p["volume_ml"] or 1000)

    # The agent reads orders.csv, which carries the INVOICED quantity. When a
    # delivery arrives short, the books believe the invoice. That belief is the
    # discrepancy, so the validator has to share it rather than peek at truth.
    received = sum(float(o["quantity"]) for o in sim.orders
                   if o["product_id"] == pid and o["actual_delivery_date"]
                   and last["date"] < o["actual_delivery_date"] < as_of)

    used = 0.0
    for s in sim.sales:
        if not (last["date"] < s["date"] < as_of):
            continue
        if s["item_type"] == "product" and s["item_id"] == pid:
            used += s["units_sold"] * serving_ml(p) / unit_ml
        elif s["item_type"] == "cocktail":
            for r in sim.recipe_by_cocktail[s["item_id"]]:
                if r["ingredient_product_id"] == pid:
                    used += s["units_sold"] * r["quantity_ml"] / unit_ml
    return {
        "last_count_date": last["date"],
        "last_count": round(last["reported_stock"], 2),
        "received": round(received, 2),
        "pos_used": round(used, 2),
        "book": round(last["reported_stock"] + received - used, 2),
    }


def truth_at(sim, pid, on):
    for g in sim.gt_stock:
        if g["date"] == on and g["product_id"] == pid:
            return g["true_closing_stock"]
    return None


def report(sim, as_of, prev_close, targets):
    rows = []
    for pid, label in targets:
        b = book_stock(sim, pid, as_of)
        t = truth_at(sim, pid, prev_close)
        rows.append({
            "product": sim.by_id[pid]["name"], "label": label,
            "count_date": b["last_count_date"], "count": b["last_count"],
            "received": b["received"], "pos_used": b["pos_used"],
            "book": b["book"], "physical": t, "gap": round(b["book"] - t, 2),
        })
    return rows
