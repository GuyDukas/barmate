"""Quality gate. One repair attempt, then ship with the concern stated.

Looping until a reviewer is satisfied is how a request runs out of time. An
answer that carries an unresolved caveat is more useful than a timeout, so the
gate runs exactly once and the outcome is recorded either way.

The reviewer is handed the raw tool observations, never the model's account of
them. Asked to check that every figure is traceable while looking only at the
agent's own thoughts, it would confirm whatever the agent invented.
"""
import json

from app import llm
from app.agent import prompts


def review(question, draft, trace):
    payload = json.dumps({
        "question": question,
        "draft_answer": draft,
        "tool_results": trace.observations,
    }, ensure_ascii=False, default=str)

    try:
        verdict = llm.chat(prompts.REFLECTOR, payload)
    except Exception as e:
        # The draft is already good enough to send. Losing it because the
        # review call failed turns a working answer into an error page.
        trace.add("Reflector", prompts.REFLECTOR, payload,
                  {"error": f"review unavailable: {type(e).__name__}: {e}"})
        return draft, None

    trace.add("Reflector", prompts.REFLECTOR, payload, verdict)
    if verdict.get("passed", True):
        return draft, True

    repair = json.dumps({
        "draft_answer": draft,
        "failures": verdict.get("failures", []),
        "critique": verdict.get("critique", ""),
        "tool_results": trace.observations,
    }, ensure_ascii=False, default=str)

    try:
        fixed = llm.chat(prompts.REVISER, repair)
    except Exception as e:
        trace.add("Reviser", prompts.REVISER, repair,
                  {"error": f"revision unavailable: {type(e).__name__}: {e}"})
        return draft, False

    trace.add("Reviser", prompts.REVISER, repair, fixed)
    return fixed.get("answer", draft), False
