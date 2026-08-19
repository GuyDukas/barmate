"""Catalogue lookup. Never guesses: an unrecognised name comes back as
not found, with no substitution and no nearest neighbour.

This is the anti-fabrication guard. A manager asking about a bottle the venue
does not carry needs to hear that it is not stocked, not a plausible number for
something else.
"""
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
    hits = [p for p in db.products() if _norm(p["category"]) == c]
    return {"category": category, "found": bool(hits), "products": [
        {"product_id": p["product_id"], "name": p["name"],
         "safety_stock": p["safety_stock"]} for p in hits]}
