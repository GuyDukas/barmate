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
TIMEOUT = 25

OK, BAD, WARN = "  ok  ", " FAIL ", " warn "


def load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


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

    tables = []
    try:
        tables = sorted((r.json().get("definitions") or {}).keys())
    except ValueError:
        pass
    if tables:
        report(OK, "Supabase", f"reachable, {len(tables)} tables: {', '.join(tables[:6])}")
    else:
        report(WARN, "Supabase", "reachable and authenticated, but no tables yet")
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


LLMOD_BASES = [
    "https://api.llmod.ai/v1",
    "https://llmod.ai/api/v1",
    "https://api.llmod.ai",
]


def check_llmod():
    key = os.environ.get("LLMOD_API_KEY")
    if not key:
        return report(BAD, "LLMod.ai", "LLMOD_API_KEY not set")

    configured = os.environ.get("LLMOD_BASE_URL")
    bases = [configured] if configured else LLMOD_BASES

    for base in bases:
        url = f"{base.rstrip('/')}/models"
        try:
            r = requests.get(url, headers={"Authorization": f"Bearer {key}"},
                             timeout=TIMEOUT)
        except Exception:
            continue
        if r.status_code < 400:
            names = []
            try:
                body = r.json()
                names = [m.get("id") for m in (body.get("data") or body) if isinstance(m, dict)]
            except ValueError:
                pass
            report(OK, "LLMod.ai", f"{base} responded, {len(names)} models")
            wanted = [n for n in names if n and "gpt-5.4-mini" in n or (n and "embedding" in n)]
            if wanted:
                print("       models we need: " + ", ".join(sorted(set(wanted))[:6]))
            if not configured:
                print(f"       add to .env:  LLMOD_BASE_URL={base}")
            return None
        if r.status_code in (401, 403):
            return report(BAD, "LLMod.ai", f"{base} rejected the key: HTTP {r.status_code}")

    report(BAD, "LLMod.ai",
           "no base URL responded. Tried: " + ", ".join(bases))
    print("       Find the base URL on the LLMod.ai dashboard and set "
          "LLMOD_BASE_URL in .env")
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
