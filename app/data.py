"""Bundle loader. Module-scope cache: Vercel reuses warm instances, so the
parse is paid once per instance, not once per request."""
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
