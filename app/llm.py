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
            r = requests.post(f"{BASE}{path}", headers=_headers(),
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


def chat(system_prompt, user_prompt, json_mode=True):
    payload = {
        "model": TEXT_MODEL,
        "messages": [{"role": "system", "content": system_prompt},
                     {"role": "user", "content": user_prompt}],
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
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        cleaned = (text.strip().removeprefix("```json").removeprefix("```")
                   .removesuffix("```"))
        return json.loads(cleaned)


def embed(text):
    data = _post("/embeddings", {"model": EMBED_MODEL, "input": text})
    usage["calls"] += 1
    return data["data"][0]["embedding"]
