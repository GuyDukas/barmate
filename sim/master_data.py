"""
Master data for the BarMate demo venue.

The bottled catalogue, costs and prices are carried over from the original
course dataset. This module adds the fields the operations knowledge base
(RAG_data.txt) assumes but the original catalogue lacked: draught keg SKUs,
station assignment, supplier ownership, case sizes, unit volumes and safety
stock.

All figures describe a SIMULATED venue.
"""
import csv
from pathlib import Path

from . import config as C

SOURCE_PRODUCTS = Path("/mnt/project/products.csv")

# ------------------------------------------------------------------ suppliers
# Delivery days and order minimums follow RAG-003. Telephone numbers from the
# source document were placeholder digits (+972-54-1234567 and similar) and are
# deliberately omitted rather than reproduced as though real.
SUPPLIERS = [
    {
        "supplier_id": "SUP01",
        "name": "Hakerem Distillers",
        "delivery_days": "Mon,Thu",
        "min_order_rule": "full_cases",
        "min_order_qty": 12,
        "categories": "gin,vodka,whiskey,rum,tequila,aperitif,liqueur,vermouth,wine",
        "contact_note": "contact details not modelled",
    },
    {
        "supplier_id": "SUP02",
        "name": "IBBL (Israel Beer Breweries Ltd)",
        "delivery_days": "Tue,Fri",
        "min_order_rule": "3_kegs_or_5_cases",
        "min_order_qty": 3,
        "categories": "beer,draught_beer",
        "contact_note": "contact details not modelled",
    },
    {
        "supplier_id": "SUP03",
        "name": "CBC (Central Bottling Company)",
        "delivery_days": "Wed,Sun",
        "min_order_rule": "min_cases",
        "min_order_qty": 10,
        "categories": "soft_drink,juice,cocktail_ingredient",
        "contact_note": "contact details not modelled",
    },
]

CATEGORY_SUPPLIER = {}
for s in SUPPLIERS:
    for cat in s["categories"].split(","):
        CATEGORY_SUPPLIER[cat] = s["supplier_id"]

# ------------------------------------------------------------------ draught SKUs
# RAG-007 maps draught lines to stations. Carlsberg runs on both bars at
# different keg sizes, which is why it appears twice.
KEGS = [
    dict(product_id="K001", name="Carlsberg 50L", name_he="קרלסברג 50 ליטר",
         station="inside", volume_ml=C.ML_KEG_50, unit_cost=700.0, safety_stock=2),
    dict(product_id="K002", name="Tuborg 50L", name_he="טובורג 50 ליטר",
         station="inside", volume_ml=C.ML_KEG_50, unit_cost=690.0, safety_stock=2),
    dict(product_id="K003", name="Carlsberg 30L", name_he="קרלסברג 30 ליטר",
         station="outside", volume_ml=C.ML_KEG_30, unit_cost=450.0, safety_stock=2),
    dict(product_id="K004", name="Malka 30L", name_he="מלכה 30 ליטר",
         station="outside", volume_ml=C.ML_KEG_30, unit_cost=480.0, safety_stock=2),
    dict(product_id="K005", name="Weihenstephan 30L", name_he="ווינשטפן 30 ליטר",
         station="outside", volume_ml=C.ML_KEG_30, unit_cost=560.0, safety_stock=2),
]
DRAUGHT_SERVING_PRICE = 32

# ------------------------------------------------------------------ stations
# RAG-007: items exclusive to one bar. Everything unlisted is stocked at both.
INSIDE_ONLY = {"Patron Silver", "Martini Rosso", "Glenfiddich 12", "Coffee Beans"}
OUTSIDE_ONLY = {"Aperol"}

# ------------------------------------------------------------------ safety stock
# Explicit floors named in RAG-004; everything else falls back to a rule.
EXPLICIT_SAFETY = {"Jameson": 3, "Campari": 2, "Red Bull": 3}
SAFETY_BY_CATEGORY = {
    "beer": 4, "soft_drink": 3, "juice": 2, "cocktail_ingredient": 2,
    "wine": 3, "gin": 2, "vodka": 2, "whiskey": 2, "rum": 2, "tequila": 2,
    "aperitif": 2, "liqueur": 1, "vermouth": 1,
}
CASE_BY_CATEGORY = {
    "beer": 24, "soft_drink": 24, "juice": 6, "cocktail_ingredient": 6,
    "wine": 12, "gin": 12, "vodka": 12, "whiskey": 12, "rum": 12,
    "tequila": 12, "aperitif": 12, "liqueur": 12, "vermouth": 12,
}

HEBREW_NAMES = {
    "Carlsberg": "קרלסברג", "Heineken": "הייניקן", "Corona": "קורונה",
    "Guinness": "גינס", "Goldstar": "גולדסטאר", "Maccabee": "מכבי",
    "Stella Artois": "סטלה ארטואה", "Erdinger": "ארדינגר",
    "Bombay Sapphire": "בומביי ספיר", "Tanqueray": "טנקרי",
    "Hendrick's": "הנדריקס", "Beefeater": "ביפיטר", "Gordon's": "גורדונס",
    "Absolut": "אבסולוט", "Finlandia": "פינלנדיה", "Grey Goose": "גריי גוס",
    "Belvedere": "בלוודר", "Stolichnaya": "סטולי",
    "Jameson": "ג'יימסון", "Jack Daniel's": "ג'ק דניאלס",
    "Johnnie Walker Black": "ג'וני ווקר בלאק", "Chivas Regal": "שיבס ריגל",
    "Maker's Mark": "מייקרס מארק", "Glenfiddich 12": "גלנפידיך 12",
    "Bacardi Carta Blanca": "בקרדי", "Havana Club 3": "הוואנה קלאב",
    "Captain Morgan": "קפטן מורגן", "Diplomatico Reserva": "דיפלומטיקו",
    "Jose Cuervo": "חוסה קוארבו", "Olmeca Blanco": "אולמקה",
    "Patron Silver": "פטרון סילבר", "Don Julio Blanco": "דון חוליו",
    "Aperol": "אפרול", "Campari": "קמפרי", "Cointreau": "קוואנטרו",
    "Kahlua": "קלואה", "Martini Rosso": "מרטיני רוסו",
    "House Red Wine": "יין אדום הבית", "House White Wine": "יין לבן הבית",
    "Rose Wine": "יין רוזה", "Prosecco": "פרוסקו", "Chardonnay": "שרדונה",
    "Coca-Cola": "קוקה קולה", "Coca-Cola Zero": "קולה זירו", "Sprite": "ספרייט",
    "Tonic Water": "טוניק", "Soda Water": "סודה", "Red Bull": "רדבול",
    "Ginger Beer": "ג'ינג'ר בير", "Orange Juice": "מיץ תפוזים",
    "Cranberry Juice": "מיץ חמוציות", "Lime Juice": "מיץ ליים",
    "Lemon Juice": "מיץ לימון", "Sugar Syrup": "סירופ סוכר",
    "Fresh Mint": "נענע", "Coffee Beans": "פולי קפה",
}


def _unit_volume(name, category, unit):
    if unit == "liter":
        return 1000
    if unit == "bunch":
        return None
    if category == "wine":
        return C.ML_WINE_BOTTLE
    if name.startswith("House "):
        return C.ML_HOUSE_BOTTLE
    if category in {"gin", "vodka", "whiskey", "rum", "tequila",
                    "aperitif", "liqueur", "vermouth"}:
        return C.ML_SPIRIT_BOTTLE
    if category == "beer":
        return 330
    if category == "soft_drink":
        return 330 if unit == "can" else 500
    return None


def build_products():
    rows = []
    with open(SOURCE_PRODUCTS, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            name, cat, unit = r["name"], r["category"], r["unit"]
            station = ("inside" if name in INSIDE_ONLY else
                       "outside" if name in OUTSIDE_ONLY else "both")
            rows.append({
                "product_id": r["product_id"],
                "name": name,
                "name_he": HEBREW_NAMES.get(name, ""),
                "category": cat,
                "unit": unit,
                "volume_ml": _unit_volume(name, cat, unit) or "",
                "unit_cost": float(r["unit_cost"]),
                "unit_price": int(r["unit_price"]),
                "station": station,
                "supplier_id": CATEGORY_SUPPLIER.get(cat, "SUP03"),
                "case_size": CASE_BY_CATEGORY.get(cat, 12),
                "is_draught": 0,
                "safety_stock": EXPLICIT_SAFETY.get(name,
                                                    SAFETY_BY_CATEGORY.get(cat, 2)),
            })

    # Espresso Martini needs a coffee input; RAG-006 assumes an espresso machine
    # on the inside bar. Added so the recipe closes against a real SKU.
    rows.append({
        "product_id": "P056", "name": "Coffee Beans", "name_he": "פולי קפה",
        "category": "cocktail_ingredient", "unit": "kg", "volume_ml": "",
        "unit_cost": 90.0, "unit_price": 0, "station": "inside",
        "supplier_id": "SUP03", "case_size": 6, "is_draught": 0, "safety_stock": 2,
    })

    for k in KEGS:
        rows.append({
            "product_id": k["product_id"], "name": k["name"],
            "name_he": k["name_he"], "category": "draught_beer", "unit": "keg",
            "volume_ml": k["volume_ml"], "unit_cost": k["unit_cost"],
            "unit_price": DRAUGHT_SERVING_PRICE, "station": k["station"],
            "supplier_id": "SUP02", "case_size": 1, "is_draught": 1,
            "safety_stock": k["safety_stock"],
        })
    return rows


# ------------------------------------------------------------------ cocktails
# Specifications transcribed from RAG-006. Quantities in millilitres, except
# mint which is counted in leaves and coffee in grams.
COCKTAILS = [
    dict(cocktail_id="C001", name="Aperol Spritz", name_he="אפרול שפריץ", price=46,
         recipe=[("P041", 90), ("P033", 60), ("P047", 30)]),
    dict(cocktail_id="C002", name="Margarita", name_he="מרגריטה", price=50,
         recipe=[("P031", 50), ("P035", 20), ("P052", 30)]),
    dict(cocktail_id="C003", name="Negroni", name_he="נגרוני", price=52,
         recipe=[("P034", 30), ("P011", 30), ("P037", 30)]),
    dict(cocktail_id="C004", name="Mojito", name_he="מוחיטו", price=48,
         recipe=[("P025", 50), ("P052", 25), ("P054", 15), ("P055", 9), ("P047", 60)]),
    dict(cocktail_id="C005", name="Espresso Martini", name_he="אספרסו מרטיני", price=54,
         recipe=[("P014", 40), ("P036", 20), ("P056", 9), ("P054", 10)]),
    dict(cocktail_id="C006", name="Gin & Tonic", name_he="ג'ין טוניק", price=42,
         recipe=[("P013", 50), ("P046", 150)]),
    dict(cocktail_id="C007", name="Long Island Iced Tea", name_he="לונג איילנד", price=58,
         recipe=[("P014", 15), ("P025", 15), ("P013", 15), ("P029", 15),
                 ("P035", 15), ("P053", 25), ("P043", 50)]),
]


def build_recipes(products):
    """Convert millilitre specs into fractions of a stock unit."""
    vol = {p["product_id"]: p["volume_ml"] for p in products}
    rows = []
    for c in COCKTAILS:
        for pid, qty in c["recipe"]:
            v = vol.get(pid)
            if pid == "P055":            # mint, counted in leaves; ~40 per bunch
                per_unit = qty / 40.0
            elif pid == "P056":          # coffee, grams out of a 1kg bag
                per_unit = qty / 1000.0
            elif v:
                per_unit = qty / float(v)
            else:
                per_unit = qty / 1000.0
            rows.append({
                "cocktail_id": c["cocktail_id"],
                "ingredient_product_id": pid,
                "quantity_ml": qty,
                "quantity_per_cocktail": round(per_unit, 6),
            })
    return rows


# ------------------------------------------------------------------ staff
# Names and authority levels follow RAG-011.
STAFF = [
    dict(staff_id="S01", name_he="רועי", name_en="Roei", role="shift_manager",
         authority="high", station="both", experience="senior"),
    dict(staff_id="S02", name_he="יעל", name_en="Yael", role="shift_manager",
         authority="high", station="both", experience="senior"),
    dict(staff_id="S03", name_he="שיר", name_en="Shir", role="bartender",
         authority="standard", station="inside", experience="senior"),
    dict(staff_id="S04", name_he="תומר", name_en="Tomer", role="bartender",
         authority="standard", station="outside", experience="mid"),
    dict(staff_id="S05", name_he="מאיה", name_en="Maya", role="bartender",
         authority="standard", station="outside", experience="mid"),
    dict(staff_id="S06", name_he="מוריאל", name_en="Muriel", role="bartender",
         authority="standard", station="inside", experience="junior"),
    dict(staff_id="S07", name_he="עידו", name_en="Ido", role="barback",
         authority="support", station="both", experience="junior"),
]
