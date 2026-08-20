"""LLMod.ai client. Retries on transient failure, accounts tokens so the $13
group budget is observable rather than discovered at the end.

BASE and the model names are constants, not configuration. They are not secrets
and they do not vary between a laptop and production, so making them
environment variables would only create six places for them to drift apart.
The API key is the part that varies and stays secret, and that is the part that
comes from the environment.
"""
import json
import os
import time

import requests

BASE = "https://api.llmod.ai/v1"
TEXT_MODEL = "MB5R2CF-azure/gpt-5.4-mini"
EMBED_MODEL = "MB5R2CF-azure/text-embedding-3-small"
TIMEOUT = 90
MAX_RETRIES = 3

# This model family refuses temperature: only 1 is accepted, so sampling
# cannot be turned down and two identical runs are genuinely two different
# runs. reasoning_effort is the one dial it does offer, and the failure it
# addresses is the expensive one -- at the default the model would work out
# which tools a multi-step question needs, write that down, and stop, leaving
# the manager holding a plan instead of an answer.
REASONING_EFFORT = "high"

# One connection pool per warm instance. Without it every call pays its own
# DNS lookup, TCP handshake and TLS negotiation, which was intermittently
# costing twenty seconds a request.
_session = requests.Session()

usage = {"prompt_tokens": 0, "completion_tokens": 0, "calls": 0}


def _headers():
    key = os.environ.get("LLMOD_API_KEY")
    if not key:
        raise RuntimeError("LLMOD_API_KEY is not set")
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _post(path, payload):
    last = None
    for attempt in range(MAX_RETRIES):
        try:
            r = _session.post(f"{BASE}{path}", headers=_headers(),
                              json=payload, timeout=TIMEOUT)
            if r.status_code >= 500:
                last = RuntimeError(f"upstream {r.status_code}")
                time.sleep(2 ** attempt)
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            last = e
            time.sleep(2 ** attempt)
    raise RuntimeError(f"LLM call failed after {MAX_RETRIES} attempts: {last}")


def chat(system_prompt, user_prompt, json_mode=True, effort=None):
    payload = {
        "model": TEXT_MODEL,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
        "reasoning_effort": effort or REASONING_EFFORT,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    data = _post("/chat/completions", payload)
    u = data.get("usage", {})
    usage["prompt_tokens"] += u.get("prompt_tokens", 0)
    usage["completion_tokens"] += u.get("completion_tokens", 0)
    usage["calls"] += 1
    text = data["choices"][0]["message"]["content"]
    if not json_mode:
        return text
    return _first_object(text)


def _first_object(text):
    """The first JSON object in the reply, whatever else came with it.

    Even in JSON mode this model sometimes emits the same object twice back to
    back, or wraps it in a fence, or adds a line of prose. json.loads rejects
    all three, and a rejected reply costs the agent an entire iteration for a
    tool call it had already got right.

    Anything with no object in it at all still raises, because the loop
    recovers by handing the failure back to the model and a silent empty dict
    would look like a considered reply.
    """
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").removesuffix("```")
        cleaned = cleaned.strip()
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        pass

    decoder = json.JSONDecoder()
    start = cleaned.find("{")
    while start != -1:
        try:
            return decoder.raw_decode(cleaned, start)[0]
        except json.JSONDecodeError:
            start = cleaned.find("{", start + 1)
    raise json.JSONDecodeError("no JSON object in reply", text, 0)


def embed(text):
    data = _post("/embeddings", {"model": EMBED_MODEL, "input": text})
    usage["calls"] += 1
    return data["data"][0]["embedding"]
