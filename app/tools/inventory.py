"""Stock position and reconciliation.

Book stock is what the paperwork implies:

    book = last reported count
         + units INVOICED as delivered since that count
         - units the POS accounts for since that count

Invoiced, not received. When a delivery lands short the books believe the
invoice, and that belief is the discrepancy. A tool that quietly substituted
the quantity actually received would erase the thing it exists to find.

Two limits are worth stating plainly, because both shape the API.

The venue last counted on 2026-06-10 and the anchor is 2026-06-14. There is no
independent physical figure for the four days in between, so `reconcile` cannot
manufacture a gap on its own: it takes the physical figure from the caller,
which in practice comes from a human -- a bartender's claim, a shift report, a
recount the manager just ran. With no such figure it says so and stops.

Materiality is derived per product, never fixed. Tanqueray moves under a bottle
between counts; Coca-Cola moves thirty. A single threshold near half a unit is
noise on one line and a crisis on the other, and after the demand rebuild it
sits inside the ordinary variation of every busy line in the book.
"""
import datetime

from app import data, db

STALE_AFTER_DAYS = 3

# Counts are reported to two decimals, so a gap below this is arithmetic dust
# rather than a signal. It only ever binds on lines that barely move.
MIN_ENVELOPE = 0.05

SPIRIT_CATS = {"gin", "vodka", "whiskey", "rum", "tequila",
               "aperitif", "liqueur", "vermouth"}

# RAG-005 doubles physical depletion during the 18:00-20:30 window on "draught
# beers and house spirits" while the till rings single. Draught and the house
# wines fall out of the catalogue; which brands sit on the well pour does not,
# so those three are named. This is venue policy, not an inference from data --
# guessing at it from price would put Olmeca on the list and leave Jose Cuervo
# off, and a wrong guess here dismisses real shrinkage as protocol.
WELL_POUR = {"P013", "P025", "P029"}  # Gordon's, Bacardi Carta Blanca, Jose Cuervo

# RAG-005 words the rule as "draught beers and house spirits", which reads as
# excluding the house wines. It does not: they are the house pour by the glass
# and the venue runs them on the same 1+1. The tool says so rather than leaving
# a caller to adjudicate the manual's wording against the stock behaviour.
HAPPY_HOUR_NOTE = (
    "Physical depletion doubles between 18:00 and 20:30 while the till rings "
    "single (RAG-005). The manual words this as draught beers and house "
    "spirits; the house wines are on the same 1+1 and behave the same way, "
    "which is why this line's ordinary variation is wide.")

# Words that turn a mention into a report of stock leaving unbooked: something
# spilled, dropped, broken, comped, walked off, swapped out or short-delivered.
LOSS_WORDS = ("פחת", "נפל", "נשבר",
              "שבר", "ברח", "בלי לשלם",
              "מקציפה", "לאירוע",
              "לא יודע מי", "פחות",
              "תרשמו", "מחליף",
              "broke", "broken", "dropped", "spilled", "spillage",
              "on the house", "comp", "walked", "short")


# ------------------------------------------------------------------ helpers

def _anchor_date():
    return data.anchor_date()


def serving_ml(p):
    """Millilitres one POS unit takes off the shelf."""
    if p["category"] == "draught_beer":
        return 330.0
    if p["category"] == "wine":
        return 150.0
    if p["category"] in SPIRIT_CATS:
        return 60.0
    return float(p["volume_ml"] or 330)  # sold as a whole unit


def _is_happy_hour_line(p):
    return (p["category"] == "draught_beer"
            or p["name"].startswith("House ")
            or p["product_id"] in WELL_POUR)


def _movement(product_id):
    """Every row that moves this product, fetched once.

    The envelope replays eighty count windows. Querying per window would turn
    one question into several hundred round trips against Postgres.
    """
    p = db.products_by_id().get(product_id)
    if not p:
        return None

    sales = list(db.select("sales", item_id=product_id))

    # Cocktails draw on the bottle without ever naming it in the sales row.
    draw = {}
    for r in db.select("cocktail_recipes", ingredient_product_id=product_id):
        draw[r["cocktail_id"]] = float(r["quantity_ml"])
    if draw:
        sales += list(db.select("sales", item_id="in.(%s)" % ",".join(sorted(draw))))

    return {
        "product": p,
        "sales": sales,
        "draw": draw,
        "counts": sorted(db.select("inventory_counts", product_id=product_id),
                         key=lambda c: c["date"]),
        "orders": db.select("orders", product_id=product_id),
    }


def _pos_units(mv, start, end):
    """Units the POS accounts for between two dates, exclusive at both ends."""
    p = mv["product"]
    unit_ml = float(p["volume_ml"] or 1000)
    serving = serving_ml(p)
    used = 0.0
    for s in mv["sales"]:
        if not start < s["date"] < end:
            continue
        if s["item_id"] == p["product_id"]:
            used += s["units_sold"] * serving / unit_ml
        else:
            used += s["units_sold"] * mv["draw"][s["item_id"]] / unit_ml
    return used


def _invoiced(mv, start, end):
    return sum(float(o["quantity"]) for o in mv["orders"]
               if o["actual_delivery_date"]
               and start < o["actual_delivery_date"] < end)


# -------------------------------------------------------------- book stock

def get_inventory(product_id, _mv=None):
    mv = _mv or _movement(product_id)
    if mv is None:
        return {"ok": False, "product_id": product_id,
                "error": f"{product_id} is not in the catalogue. "
                         "No stock figure can be given for it."}

    p = mv["product"]
    as_of = _anchor_date().isoformat()
    counts = [c for c in mv["counts"] if c["date"] < as_of]
    if not counts:
        return {"ok": False, "product_id": product_id,
                "error": f"no physical count on record for {product_id}"}

    last = counts[-1]
    invoiced = _invoiced(mv, last["date"], as_of)
    sold = _pos_units(mv, last["date"], as_of)
    book = last["reported_stock"] + invoiced - sold
    days = (_anchor_date() - datetime.date.fromisoformat(last["date"])).days

    # Carried on every stock position because it changes how the figure should
    # be read. On a happy-hour line the shelf runs ahead of the till by design,
    # and a caller who does not know that reads an ordinary evening as a loss.
    happy_hour = _is_happy_hour_line(p)

    return {
        "ok": True,
        "product_id": product_id,
        "name": p["name"],
        "name_he": p["name_he"],
        "category": p["category"],
        "unit": p["unit"],
        "station": p["station"],
        "as_of": as_of,
        "last_count_date": last["date"],
        "last_count": round(last["reported_stock"], 2),
        "counted_by": last.get("counted_by"),
        "units_invoiced_since": round(invoiced, 2),
        "units_sold_since": round(sold, 2),
        "book_stock": round(book, 2),
        "safety_stock": p["safety_stock"],
        "below_safety_stock": book < float(p["safety_stock"]),
        "days_since_count": days,
        "count_is_stale": days > STALE_AFTER_DAYS,
        "happy_hour_line": happy_hour,
        "explanation_docs": ["RAG-005"] if happy_hour else [],
        "happy_hour_note": HAPPY_HOUR_NOTE if happy_hour else None,
    }


# -------------------------------------------------------- variance envelope

def _percentile(values, q):
    if not values:
        return 0.0
    ordered = sorted(values)
    return ordered[min(int(q * len(ordered)), len(ordered) - 1)]


def variance_envelope(product_id, _mv=None):
    """How far this product's books and its counts normally disagree.

    Every closed window between two counts is replayed: what the paperwork
    said the next count should find, against what it found. The spread of
    those residuals is this line's own noise floor, and that is the only
    honest yardstick for whether today's gap means anything.

    Happy-hour lines come out wide without being told to. Doubled depletion
    against single-rung revenue shows up as a persistent shortfall, so the
    envelope absorbs it and a house wine has to run much further short than a
    shelf gin before anybody should care.
    """
    mv = _mv or _movement(product_id)
    if mv is None:
        return {"ok": False, "product_id": product_id,
                "error": f"{product_id} is not in the catalogue"}

    as_of = _anchor_date().isoformat()
    counts = [c for c in mv["counts"] if c["date"] < as_of]
    residuals = []
    for prev, curr in zip(counts, counts[1:]):
        expected = (prev["reported_stock"]
                    + _invoiced(mv, prev["date"], curr["date"])
                    - _pos_units(mv, prev["date"], curr["date"]))
        residuals.append(abs(expected - curr["reported_stock"]))

    # Why the tolerance is what it is. Without this the caller can report that
    # a gap is within normal variation but not say what makes this line's
    # normal so much wider than a shelf gin's.
    happy_hour = _is_happy_hour_line(mv["product"])

    return {
        "ok": True,
        "product_id": product_id,
        "windows": len(residuals),
        "typical_variance": round(_percentile(residuals, 0.50), 2),
        "envelope": round(max(_percentile(residuals, 0.90), MIN_ENVELOPE), 2),
        "basis": "90th percentile of the gap between consecutive physical "
                 "counts and what the books predicted for them",
        "happy_hour_line": happy_hour,
        "explanation_docs": ["RAG-005"] if happy_hour else [],
        "note": (HAPPY_HOUR_NOTE if happy_hour else
                 "No protocol widens this line; its variation is ordinary "
                 "counting error."),
    }


# ---------------------------------------------------------------- evidence

def _name_index():
    """Every catalogue name, longest first.

    Longest first matters: the Hebrew for "Carlsberg" is a strict substring of
    the Hebrew for "Carlsberg 30L", so a message about the keg also reads as a
    message about the bottle unless the more specific name wins.
    """
    names = []
    for p in db.products():
        for n in (p["name"], p.get("name_he")):
            if n:
                names.append((n.lower(), p["product_id"]))
    return sorted(names, key=lambda t: -len(t[0]))


def _mentions(text, index):
    """Product ids named in a piece of free text."""
    low = (text or "").lower()
    hits, claimed = set(), []
    for name, pid in index:  # longest first
        if name not in low:
            continue
        # A shorter name already covered by a longer one is the same mention.
        if any(name in taken for taken in claimed):
            continue
        claimed.append(name)
        hits.add(pid)
    return hits


def _evidence(since, until):
    """Chat and shift-report lines per product over a date window.

    Only lines that read as unbooked movement are kept. "The Carlsberg is
    going well tonight" names a product without reporting a loss, and counting
    it as evidence would let ordinary chatter explain away a real gap.
    """
    index = _name_index()
    found = {}

    def record(pid, key, row):
        found.setdefault(pid, {"chat": [], "shift_reports": []})[key].append(row)

    for m in db.select("whatsapp_messages"):
        if not since <= m["timestamp"][:10] <= until:
            continue
        if not any(w in m["message"] for w in LOSS_WORDS):
            continue
        for pid in _mentions(m["message"], index):
            record(pid, "chat", {"timestamp": m["timestamp"],
                                 "sender": m.get("sender"),
                                 "message": m["message"]})

    for r in db.select("shift_reports"):
        if not since <= r["date"] <= until:
            continue
        if not any(w in r["raw_report"] for w in LOSS_WORDS):
            continue
        for pid in _mentions(r["raw_report"], index):
            record(pid, "shift_reports", {"report_id": r["report_id"],
                                          "date": r["date"]})

    return found


# --------------------------------------------------------------- reconcile

def reconcile(product_id, physical_stock=None):
    """Book stock against an independent physical figure.

    `physical_stock` is whatever somebody actually saw: a recount, a
    bartender's claim, a number read out of a shift report. It is not optional
    detail -- without it there is nothing to reconcile against, and the tool
    says so rather than inventing a comparison.

    Classification follows RAG-013.
    """
    # Fetched once and shared: the position and the envelope read the same
    # rows, and against Postgres each fetch is a round trip.
    mv = _movement(product_id)
    stock = get_inventory(product_id, _mv=mv)
    if not stock["ok"]:
        return stock

    envelope = variance_envelope(product_id, _mv=mv)
    p = mv["product"]
    happy_hour = _is_happy_hour_line(p)
    window = [stock["last_count_date"], stock["as_of"]]
    evidence = _evidence(window[0], window[1]).get(
        product_id, {"chat": [], "shift_reports": []})

    result = {
        "ok": True,
        "product_id": product_id,
        "name": p["name"],
        "window": window,
        "book_stock": stock["book_stock"],
        "physical_stock": physical_stock,
        "expected_variance": envelope["envelope"],
        "happy_hour_line": happy_hour,
        "evidence": evidence,
    }

    if physical_stock is None:
        result.update({
            "gap_units": None,
            "gap_is_material": None,
            "classification": "not_computable",
            "explanation_docs": [],
            "note": f"No physical count since {window[0]}, so no gap can be "
                    "computed. Supply a counted figure, or ask for a recount.",
        })
        return result

    gap = stock["book_stock"] - float(physical_stock)
    material = abs(gap) > envelope["envelope"]

    if not material:
        classification, docs = "clean", []
        note = (f"A gap of {gap:.2f} is inside this line's ordinary variation "
                f"of {envelope['envelope']:.2f}.")
        if happy_hour:
            docs = ["RAG-005"]
            note += (" This is a happy-hour line: physical depletion doubles "
                     "between 18:00 and 20:30 while the till rings single, "
                     "which is why its ordinary variation is wide.")
    elif gap < 0:
        classification, docs = "phantom_stock", ["RAG-013"]
        note = ("More on the shelf than the books allow. RAG-013 scenario D: "
                "a miscount at close, or a delivery that arrived undocumented.")
    elif evidence["chat"] or evidence["shift_reports"]:
        classification, docs = "reported_loss", ["RAG-013"]
        note = ("RAG-013 scenario B, reported. Stock left without the books "
                "knowing, and somebody said so at the time.")
    else:
        classification, docs = "unexplained_shrinkage", ["RAG-013"]
        note = ("RAG-013 scenario B, silent. Stock left without the books "
                "knowing and nobody logged it anywhere. Needs a recount "
                "before anyone is accused of anything.")

    if happy_hour and material:
        docs = docs + ["RAG-005"]
        note += (" Happy hour widens this line's tolerance and the gap still "
                 "clears it, so the protocol does not account for this.")

    result.update({
        "gap_units": round(gap, 2),
        "gap_is_material": material,
        "classification": classification,
        "explanation_docs": docs,
        "note": note,
    })
    return result


# --------------------------------------------------------------- the sweep

def find_discrepancies():
    """Everything the books do not know about, across the whole catalogue.

    What this can find is unbooked movement somebody wrote down: a bottle
    dropped, a keg pulled, a table that walked, a delivery that arrived short.
    What it cannot find is the loss nobody mentioned, because nothing has been
    counted since it happened. That blind spot is reported rather than left to
    read as a clean bill of health.
    """
    as_of = _anchor_date().isoformat()
    # Ordered and limited rather than scanned: the counts table holds roughly
    # five thousand rows before the anchor, and PostgREST caps an unbounded
    # select at a thousand. Reading them all to take a maximum would silently
    # take the maximum of whichever thousand came back.
    latest = db.select("inventory_counts", columns="date", order="date.desc",
                       limit=1, date=f"lt.{as_of}")
    since = latest[0]["date"] if latest else as_of

    by_id = db.products_by_id()
    rows = []
    for pid, ev in sorted(_evidence(since, as_of).items()):
        if pid not in by_id:
            continue
        rows.append({"product_id": pid, "name": by_id[pid]["name"],
                     "chat": ev["chat"], "shift_reports": ev["shift_reports"]})

    return {
        "as_of": as_of,
        "unverified_since": since,
        "reviewed": len(by_id),
        "logged": rows,
        "note": (f"These are losses somebody reported between {since} and "
                 f"{as_of}. Nothing has been counted since {since}, so a loss "
                 "that was never mentioned cannot be detected from the data at "
                 "all. Treat this as the reported set, not the full set."),
    }
