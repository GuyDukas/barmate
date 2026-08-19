"""Supabase access.

Reads go over the PostgREST endpoint with `requests` rather than the Supabase
Python SDK. The SDK pulls in a dependency tree that costs cold-start time in a
serverless function to do what is, for every call here, one HTTP GET.

When SUPABASE_URL is unset the module serves the same rows from the generated
offline bundle. That is what lets the unit suite run with no network and no
credentials while the deployed agent reads from Postgres. It is a test fixture,
not a production fallback: `require_supabase()` exists so the request path can
refuse to answer from stale local data.
"""
import os
import urllib.parse

import requests

from app import data

TIMEOUT = 20

# Bundle keys that hold a flat list of rows, so the offline fixture can answer
# the same queries the database does.
_FLAT = {
    "products": "products",
    "suppliers": "suppliers",
    "staff": "staff",
    "cocktails": "cocktails",
    "cocktail_recipes": "recipes",
    "staff_schedule": "schedule",
    "shift_reports": "shift_reports",
    "whatsapp_messages": "whatsapp",
    "knowledge": "knowledge",
}

# Bundle keys grouped by a column, flattened when read as a table.
_GROUPED = {
    "sales": "sales_by_product",
    "inventory_counts": "counts_by_product",
    "orders": "orders_by_product",
    "reservations": "reservations_by_date",
    "broadcasts": "broadcasts_by_date",
}


def configured():
    return bool(os.environ.get("SUPABASE_URL") and os.environ.get("SUPABASE_SERVICE_KEY"))


def require_supabase():
    """Raise unless the database is reachable by configuration.

    The agent answering a manager's stock question from a developer's local
    fixture would be worse than failing, because the answer would look real.
    """
    if not configured():
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY are not set. "
            "The agent reads from Supabase; refusing to answer from the "
            "offline test fixture."
        )


def _headers():
    key = os.environ["SUPABASE_SERVICE_KEY"]
    return {"apikey": key, "Authorization": f"Bearer {key}"}


def select(table, columns="*", order=None, limit=None, **filters):
    """One PostgREST GET.

    Filters take PostgREST operator syntax as the value, so a caller writes
    `select("sales", date="gte.2026-06-01")`. Passing a bare value is treated
    as equality.
    """
    if not configured():
        return _from_bundle(table, order=order, limit=limit, **filters)

    params = {"select": columns}
    for column, value in filters.items():
        params[column] = value if _has_operator(value) else f"eq.{value}"
    if order:
        params["order"] = order
    if limit:
        params["limit"] = limit

    url = f"{os.environ['SUPABASE_URL'].rstrip('/')}/rest/v1/{table}"
    response = requests.get(
        url, headers=_headers(), params=urllib.parse.urlencode(params, safe=".,*()"),
        timeout=TIMEOUT,
    )
    response.raise_for_status()
    return response.json()


_OPERATORS = (
    "eq.", "neq.", "gt.", "gte.", "lt.", "lte.", "like.", "ilike.", "in.", "is.",
)


def _has_operator(value):
    return isinstance(value, str) and value.startswith(_OPERATORS)


def _rows(table):
    bundle = data.load()
    if table in _FLAT:
        return bundle[_FLAT[table]]
    if table in _GROUPED:
        return [row for group in bundle[_GROUPED[table]].values() for row in group]
    raise KeyError(f"no offline fixture for table '{table}'")


def _from_bundle(table, order=None, limit=None, **filters):
    rows = _rows(table)
    for column, value in filters.items():
        rows = [r for r in rows if _matches(r.get(column), value)]
    if order:
        column, _, direction = order.partition(".")
        rows = sorted(rows, key=lambda r: (r.get(column) is None, r.get(column)),
                      reverse=direction.startswith("desc"))
    return rows[:limit] if limit else rows


def _matches(cell, condition):
    if not _has_operator(condition):
        return str(cell) == str(condition)
    operator, _, value = condition.partition(".")
    if operator == "eq":
        return str(cell) == value
    if operator == "neq":
        return str(cell) != value
    if operator == "in":
        return str(cell) in value.strip("()").split(",")
    if operator == "is":
        return (cell is None) if value == "null" else cell is not None
    if operator in ("like", "ilike"):
        needle = value.replace("%", "").lower()
        return needle in str(cell).lower()
    if cell is None:
        return False
    comparisons = {"gt": lambda a, b: a > b, "gte": lambda a, b: a >= b,
                   "lt": lambda a, b: a < b, "lte": lambda a, b: a <= b}
    other = type(cell)(value) if not isinstance(cell, str) else value
    return comparisons[operator](cell, other)


def products():
    return select("products")


def products_by_id():
    return {p["product_id"]: p for p in products()}
