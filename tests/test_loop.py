import json

import pytest

from app import llm
from app.agent import loop


def script(monkeypatch, replies):
    """Drive the loop with a fixed sequence of model replies.

    The model is the one part that cannot be exercised offline, so it is the
    one part that gets stubbed. Everything below it -- dispatch, the trace, the
    review gate, the caps -- is the real code.
    """
    calls = []

    def fake_chat(system_prompt, user_prompt, json_mode=True):
        calls.append((system_prompt, user_prompt))
        reply = replies[min(len(calls) - 1, len(replies) - 1)]
        if isinstance(reply, Exception):
            raise reply
        return reply

    monkeypatch.setattr(llm, "chat", fake_chat)
    return calls


PASS = {"passed": True, "failures": [], "critique": ""}


def test_a_direct_answer_needs_no_tools(monkeypatch):
    script(monkeypatch, [{"thought": "known", "answer": "We open at 19:00."}, PASS])
    r = loop.run("what time do we open")
    assert r.answer == "We open at 19:00."
    assert r.meta["iterations"] == 1


def test_a_tool_result_comes_back_as_an_observation(monkeypatch):
    calls = script(monkeypatch, [
        {"thought": "check stock", "action": "get_inventory",
         "action_input": {"product_id": "K003"}},
        {"thought": "enough", "answer": "Carlsberg 30L stands at 6.08 kegs."},
        PASS,
    ])
    r = loop.run("how much carlsberg 30l is there")
    assert "6.08" in r.answer
    # The second prompt must carry what the first call returned.
    assert "6.08" in calls[1][1]
    assert r.steps[0]["response"]["observation"]["book_stock"] == 6.08


def test_knowledge_calls_are_attributed_to_the_retriever(monkeypatch):
    """The module names are a contract across the diagram, the trace and
    /api/agent_info. Retrieval that reports itself as reasoning breaks it."""
    script(monkeypatch, [
        {"thought": "look it up", "action": "list_knowledge", "action_input": {}},
        {"thought": "done", "answer": "There are 14 documents."},
        PASS,
    ])
    r = loop.run("what documents are there")
    assert r.steps[0]["module"] == "KnowledgeRetriever"
    assert r.steps[1]["module"] == "Reasoner"


def test_a_reply_with_neither_action_nor_answer_is_corrected(monkeypatch):
    calls = script(monkeypatch, [
        {"thought": "hmm"},
        {"thought": "right", "answer": "Sorted."},
        PASS,
    ])
    r = loop.run("anything")
    assert r.answer == "Sorted."
    assert "neither" in calls[1][1].lower()


def test_unparseable_json_from_the_model_does_not_kill_the_request(monkeypatch):
    """The model occasionally returns prose. That must become an observation
    the loop can recover from, not a 500 for the user."""
    script(monkeypatch, [
        json.JSONDecodeError("Expecting value", "not json", 0),
        {"thought": "ok", "answer": "Recovered."},
        PASS,
    ])
    r = loop.run("anything")
    assert r.answer == "Recovered."


def test_the_iteration_cap_is_enforced_and_declared(monkeypatch):
    """A model that keeps calling tools is the realistic way to burn the 300
    second ceiling and the group's budget."""
    script(monkeypatch, [{"thought": "again", "action": "list_knowledge",
                          "action_input": {}}])
    r = loop.run("loop forever")
    assert r.meta["hit_cap"] is True
    assert r.meta["iterations"] == loop.MAX_ITERATIONS


def test_the_wall_clock_budget_stops_the_loop(monkeypatch):
    """Five iterations at a ninety second timeout each can outlast the Vercel
    limit on their own. Iterations are not the only cap that matters."""
    monkeypatch.setattr(loop, "TIME_BUDGET_SECONDS", 0)
    script(monkeypatch, [{"thought": "again", "action": "list_knowledge",
                          "action_input": {}}])
    r = loop.run("slow")
    assert r.meta["out_of_time"] is True
    assert r.meta["iterations"] < loop.MAX_ITERATIONS


def test_token_usage_is_reported_per_request(monkeypatch):
    script(monkeypatch, [{"answer": "done"}, PASS])
    r = loop.run("anything")
    assert "llm_calls" in r.meta


# --------------------------------------------------------------- the gate

def test_the_reviewer_is_shown_tool_results_not_the_models_own_words(monkeypatch):
    """The reviewer's job is to check every number against what the tools
    returned. Handing it the model's thoughts instead would have it confirm
    the model's own inventions."""
    calls = script(monkeypatch, [
        {"thought": "check", "action": "get_inventory",
         "action_input": {"product_id": "K003"}},
        {"thought": "done", "answer": "6.08 kegs."},
        PASS,
    ])
    loop.run("how much")
    reviewer_input = json.loads(calls[-1][1])
    assert reviewer_input["tool_results"]
    assert reviewer_input["tool_results"][0]["book_stock"] == 6.08


def test_a_failed_review_is_repaired_once_then_shipped(monkeypatch):
    script(monkeypatch, [
        {"answer": "We have 400 kegs."},
        {"passed": False, "failures": ["traceable"],
         "critique": "400 appears in no tool result"},
        {"answer": "I could not establish a keg figure."},
    ])
    r = loop.run("how much")
    assert r.answer == "I could not establish a keg figure."
    assert [s["module"] for s in r.steps[-2:]] == ["Reflector", "Reviser"]
    assert r.meta["review_passed"] is False


def test_a_review_that_keeps_failing_still_ships(monkeypatch):
    """Looping until a reviewer is satisfied is how a request runs out of
    time. An answer carrying an unresolved caveat beats a timeout."""
    script(monkeypatch, [
        {"answer": "draft"},
        {"passed": False, "failures": ["traceable"], "critique": "no"},
        {"answer": "second draft"},
    ])
    r = loop.run("how much")
    assert r.answer == "second draft"
    assert sum(1 for s in r.steps if s["module"] == "Reviser") == 1


def test_a_reviewer_that_errors_does_not_lose_the_answer(monkeypatch):
    """The draft is already good enough to send. Losing it because the review
    call failed would turn a working answer into an error page."""
    script(monkeypatch, [
        {"answer": "A perfectly good answer."},
        RuntimeError("LLM call failed after 3 attempts"),
    ])
    r = loop.run("anything")
    assert r.answer == "A perfectly good answer."
    assert r.meta["review_passed"] is None


def test_the_result_carries_what_the_endpoint_returns(monkeypatch):
    script(monkeypatch, [{"answer": "fine"}, PASS])
    r = loop.run("anything")
    assert isinstance(r.answer, str)
    assert isinstance(r.steps, list)
    assert all(set(s) == {"module", "prompt", "response"} for s in r.steps)


def test_a_huge_observation_is_truncated_before_it_reaches_the_model(monkeypatch):
    calls = script(monkeypatch, [
        {"action": "get_chat", "action_input": {}},
        {"answer": "done"},
        PASS,
    ])
    loop.run("what was said")
    assert len(calls[1][1]) < 20000
