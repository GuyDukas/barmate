"""Read `.env` into the environment, without adding a dependency for it.

Vercel injects the five variables into the process, so nothing here runs in
production: there is no `.env` file in the deployment and `load_env` returns
immediately. It exists for the clone, where the file is the only place the
values live and every entry point -- the Flask app, the eval harness, the
seeding scripts -- would otherwise have to be handed them through the shell.

`setdefault` rather than assignment, so a variable already exported wins over
the file. That is the order that lets one run be pointed at a different
Supabase project without editing anything.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path=None):
    """Load ROOT/.env if it is there. A missing file is not an error."""
    path = Path(path) if path else ROOT / ".env"
    if not path.exists():
        return False
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))
    return True
