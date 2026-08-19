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
