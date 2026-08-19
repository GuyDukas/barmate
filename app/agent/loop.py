"""The ReAct loop, then the reflect gate.

Two caps, because either one alone leaves a way to overrun. The iteration cap
stops a model that keeps calling tools; the wall-clock budget stops a handful
of iterations that are each slow, which on a ninety second LLM timeout can
outlast Vercel's 300 second ceiling without ever reaching the iteration cap.

The iteration cap is eight rather than five because the multi-source questions
genuinely need it: a weekend beer order has to consult stock, sales history,
bookings, broadcasts and orders in flight before it can answer, and a cap that
forces an answer at four turns produces a confident one built on half the
evidence.

Nothing raised inside the loop escapes it. A tool error, a reply in the wrong
shape, prose where JSON was asked for -- each comes back as an observation the
model can read and recover from, because an exception here is a 500 for the
manager standing at the pass.
"""
import json
import time
from dataclasses import dataclass, field

from app import llm
from app.agent import prompts, reflect
from app.agent.trace import Trace
from app.tools.registry import run_tool

MAX_ITERATIONS = 8
TIME_BUDGET_SECONDS = 210
OBSERVATION_CHAR_LIMIT = 3500

RETRIEVAL_TOOLS = {"search_knowledge", "get_document", "list_knowledge"}


@dataclass
class Result:
    answer: str
    steps: list = field(default_factory=list)
    meta: dict = field(default_factory=dict)


def _truncate(obj):
    text = json.dumps(obj, ensure_ascii=False, default=str)
    if len(text) <= OBSERVATION_CHAR_LIMIT:
        return text
    return f"{text[:OBSERVATION_CHAR_LIMIT]}... [truncated, {len(text)} chars total]"


def _think(system, user, trace, module="Reasoner"):
    """One model call. A malformed reply is a recoverable observation."""
    try:
        return llm.chat(system, user), None
    except json.JSONDecodeError as e:
        return None, ("Observation: your reply was not valid JSON "
                      f"({e}). Reply with JSON only, in one of the two shapes.")


def run(question, trace=None):
    trace = trace or Trace()
    system = prompts.reasoner_system()
    transcript = [f"User question: {question}"]
    started = time.monotonic()
    before = dict(llm.usage)

    answer = None
    iterations = 0
    hit_cap = False
    out_of_time = False

    for i in range(MAX_ITERATIONS):
        if time.monotonic() - started > TIME_BUDGET_SECONDS:
            out_of_time = True
            break

        iterations = i + 1
        user = "\n\n".join(transcript)
        reply, complaint = _think(system, user, trace)
        if reply is None:
            transcript.append(complaint)
            continue

        action = reply.get("action")
        module = "KnowledgeRetriever" if action in RETRIEVAL_TOOLS else "Reasoner"

        if "answer" in reply:
            trace.add(module, system, user, reply)
            answer = reply["answer"]
            break

        if not action:
            trace.add(module, system, user, reply)
            transcript.append(
                "Observation: your reply had neither 'action' nor 'answer'. "
                "Reply with one of the two shapes described.")
            continue

        args = reply.get("action_input") or {}
        observation = run_tool(action, args)
        trace.add(module, system, user, reply, observation=observation)
        transcript.append(
            f"Thought: {reply.get('thought', '')}\n"
            f"Action: {action}({json.dumps(args, ensure_ascii=False, default=str)})\n"
            f"Observation: {_truncate(observation)}")
    else:
        hit_cap = True

    if answer is None:
        # Out of iterations or out of time. Ask for the best answer the
        # observations support rather than returning nothing.
        closing = ("\n\nYou have reached the limit on tool use. Answer now using "
                   "only what the observations above contain, and state plainly "
                   "anything you were unable to establish.")
        user = "\n\n".join(transcript) + closing
        reply, _ = _think(system, user, trace)
        reply = reply or {}
        trace.add("Reasoner", system, user, reply)
        answer = reply.get("answer") or (
            "I could not complete this within the tool limit. Nothing above was "
            "established firmly enough to report.")

    answer, review_passed = reflect.review(question, answer, trace)

    return Result(answer=answer, steps=trace.as_list(), meta={
        "iterations": iterations,
        "hit_cap": hit_cap,
        "out_of_time": out_of_time,
        "review_passed": review_passed,
        "tools_called": len(trace.observations),
        "llm_calls": llm.usage["calls"] - before["calls"],
        "prompt_tokens": llm.usage["prompt_tokens"] - before["prompt_tokens"],
        "completion_tokens": llm.usage["completion_tokens"] - before["completion_tokens"],
        "seconds": round(time.monotonic() - started, 2),
    })
