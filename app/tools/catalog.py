"""Catalogue lookup.

A product name is never guessed at. An unrecognised one comes back as not
found, with no substitution and no nearest neighbour, because this is the
anti-fabrication guard: a manager asking about a bottle the venue does not
carry needs to hear that it is not stocked, not a plausible number for
something else.

A category is different. It is a shelf label rather than a thing that can be
counted, so a near miss is resolved and the correction reported. 'whisky' and
'whiskey' are the same six bottles, and refusing to connect them had the agent
telling a manager the venue stocks no whisky.
"""
import difflib

from app import db


def _norm(s):
    return (s or "").strip().lower()


def resolve_product(query):
    q = _norm(query)
    if not q:
        return {"found": False, "matches": [], "note": "empty query"}

    exact, partial = [], []
    for p in db.products():
        names = [_norm(p["name"]), _norm(p.get("name_he"))]
        if q in names or p["product_id"].upper() == query.strip().upper():
            exact.append(p)
        elif any(n and (q in n or n in q) for n in names):
            partial.append(p)

    # Exact first, but never instead of. "Carlsberg" matches the bottle exactly
    # and the 30L and 50L kegs partially; returning only the bottle would hide
    # the keg SKUs, and the keg is what runs out before a football night.
    hits = exact + [p for p in partial if p not in exact]
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
    products = db.products()
    available = sorted({p["category"] for p in products})
    hits = [p for p in products if _norm(p["category"]) == c]

    # 'whisky' and 'whiskey' are the same shelf. Without this the agent asks
    # for one, gets nothing, and reports that the venue has no whisky --
    # turning a spelling difference into a false claim about the stock list.
    suggestion = None
    if not hits and c:
        near = difflib.get_close_matches(c, available, n=1, cutoff=0.8)
        if near:
            suggestion = near[0]
            hits = [p for p in products if p["category"] == suggestion]

    # Beer is one word and two categories here. An agent asked how much beer to
    # order that resolves 'beer' alone forecasts the bottles and silently
    # ignores five kegs, which is most of what a bar with draught lines sells.
    resolved = suggestion or (c if hits else None)
    related = sorted({"beer", "draught_beer"} - {resolved}) if resolved in (
        "beer", "draught_beer") else []

    return {
        "category": category,
        "found": bool(hits),
        "did_you_mean": suggestion,
        "related_categories": related,
        "available_categories": available,
        "products": [{"product_id": p["product_id"], "name": p["name"],
                      "safety_stock": p["safety_stock"]} for p in hits],
    }


def resolve_supplier(query):
    """The supplier, and everything the venue buys from them.

    A manager naming a supplier -- "did we get everything we paid for from
    CBC?" -- is asking about fourteen products at once, and nothing else in the
    registry maps a company to its lines. Without this the agent puts CBC to
    resolve_category, is told the venue has no such category, and answers a
    question about a delivery by listing shelf labels.

    Matched on the short name as well as the registered one, because that is
    how the invoices and the staff refer to them: nobody says Central Bottling
    Company.
    """
    q = _norm(query)
    suppliers = db.select("suppliers")
    if not q:
        return {"found": False, "suppliers": [
            {"supplier_id": s["supplier_id"], "name": s["name"]} for s in suppliers]}

    hits = []
    for s in suppliers:
        name = _norm(s["name"])
        # "CBC (Central Bottling Company)" -> also match "CBC" and the words
        # inside the bracket, either of which is what gets typed.
        parts = {name, name.split("(")[0].strip(),
                 name.partition("(")[2].rstrip(")").strip()}
        if (s["supplier_id"].upper() == query.strip().upper()
                or any(p and (q == p or q in p or p in q) for p in parts if p)):
            hits.append(s)

    if not hits:
        return {"found": False, "suppliers": [
            {"supplier_id": s["supplier_id"], "name": s["name"]} for s in suppliers],
            "note": f"'{query}' is not a supplier the venue buys from."}

    products = db.products()
    return {"found": True, "suppliers": [{
        "supplier_id": s["supplier_id"],
        "name": s["name"],
        "delivery_days": s["delivery_days"],
        "min_order_rule": s["min_order_rule"],
        "min_order_qty": s["min_order_qty"],
        "categories": s["categories"],
        "products": [{"product_id": p["product_id"], "name": p["name"],
                      "category": p["category"]}
                     for p in products if p["supplier_id"] == s["supplier_id"]],
    } for s in hits],
        # Asked whether a supplier delivered everything invoiced, the agent has
        # been observed reading this list, finding it complete, and answering
        # yes -- citing a shift report it never opened. The list is who sells
        # what. It is not evidence about any delivery.
        "answers_who_sells_what_only": (
            "This is the supply relationship, not the delivery. Whether "
            "everything invoiced actually arrived is answered by reconciling "
            "these product lines against the books and reading what staff "
            "reported, never from this list and never from the manual.")}
