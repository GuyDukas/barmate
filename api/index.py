"""Flask app. Four endpoints with exact names, plus the GUI at root.

The agent modules are imported inside execute() rather than at module scope.
That is deliberate: it keeps the deployment working while the agent is still
being built, so Vercel configuration, the Python version and the dependency
install are all proven before the interesting code lands. An import error here
would take down team_info and the architecture diagram along with it.
"""
import json
import sys
import traceback
from pathlib import Path

from flask import Flask, Response, jsonify, request, send_from_directory

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"

# Vercel invokes this file directly, so the repository root is not necessarily
# on the path when `app` is imported.
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

app = Flask(__name__, static_folder=str(STATIC))
app.json.ensure_ascii = False  # Hebrew appears throughout the data and answers.

TEAM = {
    "group_batch_order_number": "2_8",
    "team_name": "BarMate",
    "students": [
        {"name": "Guy Dukas", "email": "guy.dukas@gmail.com", "id": "326692662"},
        {"name": "Reut Ness", "email": "Reutness2799@gmail.com", "id": "318738127"},
        {"name": "Yuval Belelovsky", "email": "Ybelelovsky@campus.technion.ac.il",
         "id": "326156536"},
    ],
}

AGENT_INFO = json.loads((STATIC / "agent_info.json").read_text(encoding="utf-8"))


def _error(message, status=200):
    return jsonify({"status": "error", "error": message,
                    "response": None, "steps": []}), status


@app.get("/")
def gui():
    return send_from_directory(STATIC, "index.html")


@app.get("/api/team_info")
def team_info():
    return jsonify(TEAM)


@app.get("/api/agent_info")
def agent_info():
    return jsonify(AGENT_INFO)


@app.get("/api/model_architecture")
def model_architecture():
    return Response((STATIC / "architecture.png").read_bytes(), mimetype="image/png")


@app.get("/api/health")
def health():
    """Which services are wired up. Not required by the brief, but the fastest
    way to tell a missing environment variable from a broken deployment.

    ?deep=1 actually queries them. A variable being present says nothing about
    whether the function can reach the service: a wrong region, a paused
    project or a network rule all look identical from the environment alone.
    """
    import os
    status = {
        "supabase": bool(os.environ.get("SUPABASE_URL")),
        "pinecone": bool(os.environ.get("PINECONE_INDEX_HOST")),
        "llmod": bool(os.environ.get("LLMOD_API_KEY")),
        "agent_available": _agent() is not None,
    }
    if not request.args.get("deep"):
        return jsonify(status)

    checks = {}
    try:
        from app import db
        checks["supabase"] = f"{len(db.select('products', limit=1))} row read"
    except Exception as e:
        checks["supabase"] = f"{type(e).__name__}: {e}"

    try:
        from app import vectors
        checks["pinecone"] = f"{vectors.count()} vectors"
    except Exception as e:
        checks["pinecone"] = f"{type(e).__name__}: {e}"

    try:
        from app import llm
        checks["llmod"] = f"embedding dim {len(llm.embed('ping'))}"
    except Exception as e:
        checks["llmod"] = f"{type(e).__name__}: {e}"

    status["deep"] = checks
    return jsonify(status)


def _agent():
    """The ReAct loop, or None while it is still being built."""
    try:
        from app.agent import loop
        return loop
    except ImportError:
        return None


@app.post("/api/execute")
def execute():
    body = request.get_json(silent=True) or {}
    prompt = (body.get("prompt") or "").strip()
    if not prompt:
        return _error("prompt is required")

    agent = _agent()
    if agent is None:
        return _error("The agent loop is not deployed yet. The data layer, "
                      "tools and endpoints are live; POST /api/execute starts "
                      "answering once the Reasoner ships.")

    try:
        result = agent.run(prompt)
        return jsonify({"status": "ok", "error": None,
                        "response": result.answer, "steps": result.steps})
    except Exception as e:
        app.logger.error(traceback.format_exc())
        return _error(f"{type(e).__name__}: {e}")


if __name__ == "__main__":
    app.run(debug=True, port=5000)
