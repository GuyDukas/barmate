#!/usr/bin/env python3
"""Check the four endpoints against the brief, field by field.

    python scripts/check_endpoints.py                    # the local Flask app
    python scripts/check_endpoints.py https://<url>      # the deployment

The unit tests check this code against itself. This checks it against the
specification: every key name, every type, every required field, quoted from
the brief rather than restated. The two are not the same audit, and the one
that catches a renamed key is this one -- prompt_examples[].response looked
entirely correct until you read that the brief calls it full_response.

--execute additionally posts a prompt and validates the live response envelope.
It is off by default because it costs a model call against a shared budget.
"""
import argparse
import json
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OK, BAD = "PASS", "FAIL"
failures = []


def report(status, endpoint, detail):
    print(f"  [{status}] {endpoint:26} {detail}")
    if status == BAD:
        failures.append(f"{endpoint}: {detail}")


def check(condition, endpoint, detail):
    report(OK if condition else BAD, endpoint, detail)
    return condition


class Local:
    """The Flask app in-process, so the check runs with no server and no network."""

    def __init__(self):
        from api.index import app
        app.config["TESTING"] = True
        self.client = app.test_client()

    def get(self, path):
        r = self.client.get(path)
        return r.status_code, dict(r.headers), r.data

    def post(self, path, body):
        r = self.client.post(path, json=body)
        return r.status_code, dict(r.headers), r.data


class Remote:
    def __init__(self, base):
        self.base = base.rstrip("/")

    def _open(self, req):
        with urllib.request.urlopen(req, timeout=300) as r:
            return r.status, dict(r.headers), r.read()

    def get(self, path):
        return self._open(urllib.request.Request(self.base + path))

    def post(self, path, body):
        return self._open(urllib.request.Request(
            self.base + path, data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST"))


def as_json(raw):
    return json.loads(raw.decode("utf-8"))


# --------------------------------------------------------- A) team_info

def team_info(http):
    """"group_batch_order_number": "{batch#}_{order#}", "team_name", "students"
    with name and email."""
    status, headers, raw = http.get("/api/team_info")
    if not check(status == 200, "GET /api/team_info", f"HTTP {status}"):
        return
    check("application/json" in headers.get("Content-Type", ""),
          "GET /api/team_info", "Content-Type is JSON")
    d = as_json(raw)
    check({"group_batch_order_number", "team_name", "students"} <= set(d),
          "GET /api/team_info", "has the three required keys")
    check("_" in d["group_batch_order_number"],
          "GET /api/team_info",
          f"batch_order is {d['group_batch_order_number']!r}, shaped batch_order")
    students = d.get("students", [])
    check(bool(students) and all({"name", "email"} <= set(s) for s in students),
          "GET /api/team_info", f"{len(students)} students, each with name and email")
    check(all("@" in s.get("email", "") for s in students),
          "GET /api/team_info", "every email is filled in")


# -------------------------------------------------------- B) agent_info

def agent_info(http):
    """description, purpose, prompt_template.template, and prompt_examples
    each carrying prompt, full_response and steps."""
    status, headers, raw = http.get("/api/agent_info")
    if not check(status == 200, "GET /api/agent_info", f"HTTP {status}"):
        return
    d = as_json(raw)

    check({"description", "purpose", "prompt_template", "prompt_examples"} <= set(d),
          "GET /api/agent_info", "description, purpose, prompt_template, prompt_examples")
    check(isinstance(d.get("prompt_template"), dict)
          and "template" in d["prompt_template"],
          "GET /api/agent_info", "prompt_template is an object with a template")

    examples = d.get("prompt_examples") or []
    check(bool(examples), "GET /api/agent_info", f"{len(examples)} worked examples")

    # The brief writes full_response. It is the field most easily got wrong,
    # because "response" reads correct and is not what was asked for.
    missing = [i for i, e in enumerate(examples)
               if not {"prompt", "full_response", "steps"} <= set(e)]
    check(not missing, "GET /api/agent_info",
          "every example has prompt, full_response and steps"
          + (f" -- missing in {missing}" if missing else ""))

    bad_steps, bad_prompt = [], []
    for i, e in enumerate(examples):
        for j, s in enumerate(e.get("steps") or []):
            if not {"module", "prompt", "response"} <= set(s):
                bad_steps.append(f"{i}.{j}")
            elif set(s["prompt"]) != {"System_prompt", "User_prompt"}:
                bad_prompt.append(f"{i}.{j}")
    check(not bad_steps, "GET /api/agent_info",
          "every step has module, prompt and response"
          + (f" -- {bad_steps}" if bad_steps else ""))
    check(not bad_prompt, "GET /api/agent_info",
          "every step prompt is System_prompt + User_prompt"
          + (f" -- {bad_prompt}" if bad_prompt else ""))

    # "All sub-modules / sub-agents names must be consistent across the
    # architecture diagram, your steps logging, and any descriptions."
    from app.agent.trace import VALID_MODULES
    seen = {s["module"] for e in examples for s in e.get("steps", [])}
    check(seen <= VALID_MODULES, "GET /api/agent_info",
          f"modules {sorted(seen)} all appear on the diagram")


# ----------------------------------------------- C) model_architecture

def model_architecture(http):
    """Content-Type: image/png, body: the PNG file. The one endpoint that is
    deliberately not JSON."""
    status, headers, raw = http.get("/api/model_architecture")
    if not check(status == 200, "GET /api/model_architecture", f"HTTP {status}"):
        return
    check(headers.get("Content-Type") == "image/png",
          "GET /api/model_architecture",
          f"Content-Type is {headers.get('Content-Type')!r}")
    check(raw[:8] == b"\x89PNG\r\n\x1a\n", "GET /api/model_architecture",
          f"body is a PNG ({len(raw):,} bytes)")


# ----------------------------------------------------------- D) execute

def execute(http, live=False):
    """Exactly these top-level fields, on the success path and the error path.
    The error path is where a hand-written response usually drifts."""
    status, headers, raw = http.post("/api/execute", {"prompt": ""})
    d = as_json(raw)
    check(set(d) == {"status", "error", "response", "steps"},
          "POST /api/execute", f"error envelope is exactly {sorted(d)}")
    check(d["status"] == "error" and d["response"] is None and d["steps"] == [],
          "POST /api/execute", "empty prompt -> status error, response null, steps []")

    if not live:
        report("SKIP", "POST /api/execute", "run with --execute to spend a model call")
        return

    status, headers, raw = http.post(
        "/api/execute", {"prompt": "How much Macallan do we have left?"})
    d = as_json(raw)
    check(set(d) == {"status", "error", "response", "steps"},
          "POST /api/execute", f"success envelope is exactly {sorted(d)}")
    if d["status"] != "ok":
        report(BAD, "POST /api/execute", f"agent returned an error: {d['error']}")
        return
    check(isinstance(d["response"], str) and d["response"].strip(),
          "POST /api/execute", "response is a non-empty string")
    steps = d["steps"]
    check(bool(steps), "POST /api/execute", f"{len(steps)} steps traced")
    shapes = all(set(s) == {"module", "prompt", "response"}
                 and set(s["prompt"]) == {"System_prompt", "User_prompt"}
                 for s in steps)
    check(shapes, "POST /api/execute", "every step matches the required schema")


# ---------------------------------------------------------------- GUI

def gui(http):
    """A textarea, a Run Agent button, the response, the full steps trace, and
    no authentication between the grader and any of it."""
    status, headers, raw = http.get("/")
    if not check(status == 200, "GET /", f"HTTP {status}"):
        return
    html = raw.decode("utf-8")
    check("text/html" in headers.get("Content-Type", ""), "GET /", "serves HTML")
    check("<textarea" in html, "GET /", "has a textarea")
    check("Run Agent" in html, "GET /", "has a Run Agent button")
    check("/api/execute" in html, "GET /", "posts to /api/execute")
    check("System_prompt" in html and "Response" in html,
          "GET /", "renders module, prompt and response for each step")
    check(not any(w in html.lower() for w in
                  ("sign in", "log in", "login", "password", "auth0")),
          "GET /", "no authentication guard")


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("base", nargs="?",
                    help="deployment URL; omitted, the local app is checked")
    ap.add_argument("--execute", action="store_true",
                    help="also POST a real prompt (costs one model call)")
    args = ap.parse_args(argv)

    http = Remote(args.base) if args.base else Local()
    print(f"\nBarMate endpoint conformance -- {args.base or 'local Flask app'}\n")

    team_info(http)
    agent_info(http)
    model_architecture(http)
    execute(http, live=args.execute)
    gui(http)

    if failures:
        print(f"\n  {len(failures)} check(s) failed:")
        for f in failures:
            print(f"    - {f}")
        return 1
    print("\n  every endpoint matches the brief.\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
