"""Free-text sources: closing reports and the shift group chat.

Claims are extracted, never resolved. Where a bartender wrote a bare number
with no word saying whether it is what went or what is left, the tool returns
both readings and marks it ambiguous, because the information needed to choose
is genuinely absent from the message. Guessing produces a figure that looks
exactly as solid as a real one.

What comes out of here feeds reconciliation: a `units_remaining` claim is an
independent physical figure, which is the one input the books cannot supply.
"""
import re

from app import db

# A percentage is not a stock figure. Reports close with "I'm not 100% sure",
# and reading that 100 as a count puts a phantom hundred units on whichever
# product was named last.
PERCENT = re.compile(r"\d+(?:\.\d+)?\s*%")
NUMBER = re.compile(r"\d+(?:\.\d+)?")

# "We went through about 8 of Stella Artois tonight" is the one report form
# that puts the figure ahead of the product. Every other form, in both
# languages, names the product first.
LEADING = re.compile(r"(\d+(?:\.\d+)?)\s+of\s+$", re.IGNORECASE)

USED_HINTS = ("used", "went through", "finished", "we finished",
              "ירדו", "ירד", "השתמשנו", "סיימנו", "במשמרת")
LEFT_HINTS = ("left", "remaining", "in stock", "counted", "theres", "there's",
              "נשארו", "ספרתי", "במלאי")


def _name_index():
    """Catalogue names, longest first, so 'Carlsberg 30L' wins over
    'Carlsberg' where both match the same run of text."""
    names = []
    for p in db.products():
        for n in (p["name"], p.get("name_he")):
            if n:
                names.append((n.lower(), p["product_id"]))
    return sorted(names, key=lambda t: -len(t[0]))


def _mentions(text, index):
    """Non-overlapping (start, end, product_id) spans, in reading order."""
    low, taken, found = text.lower(), [], []
    for name, pid in index:
        start = 0
        while True:
            at = low.find(name, start)
            if at < 0:
                break
            if not any(a <= at < b for a, b in taken):
                taken.append((at, at + len(name)))
                found.append((at, at + len(name), pid))
            start = at + 1
    found.sort()
    return found


def _claims(text, index):
    """One claim per product named, with the figures written beside it."""
    masked = PERCENT.sub(lambda m: " " * len(m.group()), text)
    spans = _mentions(masked, index)
    products = {pid for _, _, pid in spans}
    by_id = db.products_by_id()

    claims = []
    for k, (start, end, pid) in enumerate(spans):
        stop = spans[k + 1][0] if k + 1 < len(spans) else len(masked)
        window = masked[end:stop]
        values = [float(m.group()) for m in NUMBER.finditer(window)]

        # Pull back a figure written ahead of the product name, and take it off
        # the previous product, which would otherwise read it as its own.
        lead = LEADING.search(masked[:start])
        if lead:
            values.insert(0, float(lead.group(1)))
        ahead = LEADING.search(masked[:stop]) if k + 1 < len(spans) else None
        if ahead and values and values[-1] == float(ahead.group(1)):
            values.pop()

        low = window.lower()
        used = remaining = None
        ambiguous = False
        if len(values) >= 2:
            # Every report form in use writes what went before what is left.
            used, remaining = values[0], values[1]
        elif len(values) == 1:
            if any(h in low for h in LEFT_HINTS):
                remaining = values[0]
            elif any(h in low for h in USED_HINTS):
                used = values[0]
            else:
                ambiguous = True

        claims.append({
            "product_id": pid,
            "name": by_id[pid]["name"] if pid in by_id else pid,
            "value": values[0] if values else None,
            "units_used": used,
            "units_remaining": remaining,
            "ambiguous": ambiguous,
            "possible_meanings": (["units_used", "units_remaining"] if ambiguous
                                  else [k for k, v in (("units_used", used),
                                                       ("units_remaining", remaining))
                                        if v is not None]),
            "snippet": text[start:min(len(text), stop)].strip()[:120],
        })
    assert products == {c["product_id"] for c in claims}
    return claims


def get_shift_reports(date_from=None, date_to=None, product_id=None, limit=10):
    index = _name_index()
    out = []
    for r in sorted(db.select("shift_reports"), key=lambda r: r["date"]):
        if date_from and r["date"] < date_from:
            continue
        if date_to and r["date"] > date_to:
            continue
        claims = _claims(r["raw_report"], index)
        if product_id and not any(c["product_id"] == product_id for c in claims):
            continue
        out.append({
            "report_id": r["report_id"],
            "date": r["date"],
            "author": r.get("author_name"),
            "language": r.get("language"),
            "raw_report": r["raw_report"],
            "claims": claims,
        })
    return {"ok": True, "count": len(out),
            "reports": out[-limit:] if limit else out}


def get_chat(date_from=None, date_to=None, product_id=None, limit=40):
    index = _name_index()
    messages = []
    for m in sorted(db.select("whatsapp_messages"), key=lambda m: m["timestamp"]):
        day = m["timestamp"][:10]
        if date_from and day < date_from:
            continue
        if date_to and day > date_to:
            continue
        if product_id and product_id not in {
                pid for _, _, pid in _mentions(m["message"], index)}:
            continue
        messages.append(m)
    return {"ok": True, "count": len(messages),
            "messages": messages[-limit:] if limit else messages}
