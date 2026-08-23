#!/usr/bin/env python3
"""
Confirm every external service answers before building against it.

    python scripts/check_services.py

Reads .env when present, otherwise the ambient environment, so the same script
verifies a laptop and a deployed function. Prints no secret values.
"""
import json
import os
import sys
import urllib.parse
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.env import load_env  # noqa: E402  -- needs ROOT on the path first

TIMEOUT = 25

OK, BAD, WARN = "  ok  ", " FAIL ", " warn "


def report(status, name, detail):
    print(f"[{status}] {name:<26} {detail}")


def check_supabase():
    url, key = os.environ.get("SUPABASE_URL"), os.environ.get("SUPABASE_SERVICE_KEY")
    if not (url and key):
        return report(BAD, "Supabase", "SUPABASE_URL or SUPABASE_SERVICE_KEY not set")
    try:
        r = requests.get(f"{url.rstrip('/')}/rest/v1/",
                         headers={"apikey": key, "Authorization": f"Bearer {key}"},
                         timeout=TIMEOUT)
    except Exception as e:
        return report(BAD, "Supabase", f"{type(e).__name__}: {e}")
    if r.status_code >= 400:
        return report(BAD, "Supabase", f"HTTP {r.status_code}: {r.text[:120]}")

    # PostgREST emits Swagger 2 (definitions) or OpenAPI 3
    # (components.schemas) depending on version, and neither key is present at
    # all when the schema is empty. paths is the one signal common to both.
    tables = []
    try:
        body = r.json()
        tables = sorted(
            (body.get("definitions") or {})
            or ((body.get("components") or {}).get("schemas") or {})
            or {p.lstrip("/") for p in (body.get("paths") or {}) if p != "/"}
        )
    except ValueError:
        pass

    if not tables:
        report(BAD, "Supabase", "authenticated, but no tables. Run db/schema.sql "
                                "in the SQL Editor")
    elif len(tables) < 16:
        report(WARN, "Supabase",
               f"only {len(tables)}/16 tables: {', '.join(tables)}")
    else:
        report(OK, "Supabase", f"{len(tables)} tables present")
    return None


def check_pinecone():
    host, key = os.environ.get("PINECONE_INDEX_HOST"), os.environ.get("PINECONE_API_KEY")
    if not (host and key):
        return report(BAD, "Pinecone", "PINECONE_INDEX_HOST or PINECONE_API_KEY not set")
    host = host.replace("https://", "").rstrip("/")
    try:
        r = requests.post(f"https://{host}/describe_index_stats",
                          headers={"Api-Key": key, "Content-Type": "application/json"},
                          json={}, timeout=TIMEOUT)
    except Exception as e:
        return report(BAD, "Pinecone", f"{type(e).__name__}: {e}")
    if r.status_code >= 400:
        return report(BAD, "Pinecone", f"HTTP {r.status_code}: {r.text[:160]}")

    stats = r.json()
    dim = stats.get("dimension")
    count = stats.get("totalVectorCount", 0)
    detail = f"reachable, dimension={dim}, vectors={count}"
    if dim != 1536:
        return report(BAD, "Pinecone",
                      f"{detail}. text-embedding-3-small needs 1536; "
                      "the index must be recreated")
    report(OK, "Pinecone", detail)
    return None


def check_llmod():
    """Verifies the endpoint and models app/llm.py is hardcoded to use, so a
    green check here means the agent will reach the same place."""
    if not os.environ.get("LLMOD_API_KEY"):
        return report(BAD, "LLMod.ai", "LLMOD_API_KEY not set")

    from app import llm

    try:
        r = requests.get(f"{llm.BASE}/models", headers=llm._headers(), timeout=TIMEOUT)
    except Exception as e:
        return report(BAD, "LLMod.ai", f"{llm.BASE}: {type(e).__name__}: {e}")
    if r.status_code in (401, 403):
        return report(BAD, "LLMod.ai", f"key rejected: HTTP {r.status_code}")
    if r.status_code >= 400:
        return report(BAD, "LLMod.ai", f"HTTP {r.status_code}: {r.text[:120]}")

    body = r.json()
    names = {m.get("id") for m in (body.get("data") or body) if isinstance(m, dict)}
    report(OK, "LLMod.ai", f"{llm.BASE} responded, {len(names)} models")

    for label, wanted in (("text", llm.TEXT_MODEL), ("embeddings", llm.EMBED_MODEL)):
        mark = "present" if wanted in names else "NOT OFFERED BY THIS KEY"
        print(f"       {label:<11} {wanted}  {mark}")
    return None


def main():
    load_env()
    print()
    check_supabase()
    check_pinecone()
    check_llmod()
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
