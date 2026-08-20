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

# These answer "does the venue carry this and what is it called". None of them
# reads a record. An answer whose only tool calls are lookups has consulted the
# catalogue and nothing else, however confident it sounds.
LOOKUP_TOOLS = {"resolve_product", "resolve_category", "resolve_supplier",
                "list_knowledge"}

# The failure this catches, in the model's own words: "I still need to compare
# the book stock with a physical recount. If you want, I can do that comparison
# now." The manager already asked for it. The prompt forbids this and the model
# does it anyway on roughly a third of runs, so the loop stops accepting it:
# temperature is fixed at 1 for this model family and cannot be turned down,
# which makes prompt wording alone an unreliable place to enforce anything.
OFFERS_TO_CONTINUE = (
    "if you want", "if you'd like", "if you would like", "would you like me",
    "shall i", "want me to", "i can check", "i can do that", "i can run",
    "i can pull", "i'll check", "i will check", "let me know if",
    "אם תרצה", "אם תרצי", "רוצה שאבדוק", "אבדוק עכשיו",
)

MAX_PUSHBACKS = 2

# Follow-ups are optional in the brief, and required in practice by this agent:
# it is built to ask which of two readings of a figure was meant, and a manager
# who cannot answer that question is left with a clarification and no way to
# resolve it. Three turns rather than the whole conversation, and the earlier
# answers trimmed, because context costs money on a shared budget and the turn
# before last is rarely what a follow-up refers to.
HISTORY_TURNS = 3
HISTORY_ANSWER_CHARS = 700


def _history_block(history):
    """Prior turns as one block of context, or None when this is turn one."""
    turns = [t for t in (history or [])
             if isinstance(t, dict) and (t.get("prompt") or "").strip()]
    if not turns:
        return None
    lines = ["Earlier in this conversation, oldest first:"]
    for t in turns[-HISTORY_TURNS:]:
        answer = (t.get("answer") or "").strip()
        if len(answer) > HISTORY_ANSWER_CHARS:
            answer = answer[:HISTORY_ANSWER_CHARS] + " [...]"
        lines.append("Manager: " + t["prompt"].strip() + "\nBarMate: " + answer)
    lines.append(
        "The question below continues that conversation. A short reply -- a "
        "bare number, a product name, 'the first one' -- is answering what you "
        "last asked, so read it against your own question rather than as a new "
        "request. You still resolve and check everything from the tools; "
        "nothing above is evidence.")
    return "\n\n".join(lines)


def _settled(observations):
    """A lookup that came back empty is a finding, not a false start.

    resolve_product("Macallan") returning found=false has answered the
    question completely: the venue does not carry it, there is no record to
    read, and saying so is the required behaviour. Pushing that draft back
    sends the agent looking for a record that cannot exist, and the answer it
    comes back with is worse than the one it had.
    """
    return any(isinstance(o, dict) and o.get("found") is False
               for o in observations)


def _pushback(answer, called, observations):
    """Why this draft is not yet an answer, or None if it is one.

    Deterministic, and deliberately so. Both conditions are things the system
    prompt already forbids; this is the second line, where the rule is enforced
    rather than requested.
    """
    if _settled(observations):
        return None

    # Nothing at all has been called. Even the clarifying question the prompt
    # requires depends on the product existing: "Bacardi Carta Blanca 12.5"
    # answered with "could you clarify what you mean?" is not the required
    # behaviour, it is the agent declining to look the bottle up first.
    if not called:
        return (
            "Observation: you have called no tools at all. Resolve every "
            "product, category or supplier the question names before deciding "
            "anything -- a clarifying question back to the manager included, "
            "because asking which reading of a figure was meant still depends "
            "on knowing the venue carries it. Then call the tool that answers "
            "the question.")

    # A clarifying question is the answer to an ambiguous question, not a way
    # of avoiding one. Once the product is resolved, the prompt requires it and
    # the guard must not undo it.
    asks_back = "?" in answer

    if not (set(called) - LOOKUP_TOOLS) and not asks_back:
        return (
            f"Observation: you have called {', '.join(called)}, which resolves "
            "names and reads no record. If the ledger can answer this question, "
            "call the tool that answers it now. Answer with no record read only "
            "when there is no record to read.")

    low = answer.lower().replace("’", "'")
    if any(phrase in low for phrase in OFFERS_TO_CONTINUE):
        return (
            "Observation: your draft offers to do work instead of doing it. You "
            "have the tools and the manager has already asked. Make the calls "
            "you just described and then answer with what they returned. "
            "Asking which of two readings of an ambiguous figure was meant is "
            "not this, and stays.")
    return None


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


def run(question, trace=None, history=None):
    trace = trace or Trace()
    system = prompts.reasoner_system()
    earlier = _history_block(history)
    transcript = ([earlier] if earlier else []) + [f"User question: {question}"]
    started = time.monotonic()
    before = dict(llm.usage)

    answer = None
    iterations = 0
    hit_cap = False
    out_of_time = False
    called = []
    pushbacks = 0

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
            complaint = (_pushback(reply["answer"] or "", called,
                                   trace.observations)
                         if pushbacks < MAX_PUSHBACKS else None)
            if complaint:
                pushbacks += 1
                transcript.append(complaint)
                continue
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
        called.append(action)
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
        "pushbacks": pushbacks,
        "llm_calls": llm.usage["calls"] - before["calls"],
        "prompt_tokens": llm.usage["prompt_tokens"] - before["prompt_tokens"],
        "completion_tokens": llm.usage["completion_tokens"] - before["completion_tokens"],
        "seconds": round(time.monotonic() - started, 2),
    })
