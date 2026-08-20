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


def test_an_answer_that_read_no_record_is_sent_back_once(monkeypatch):
    """The regression that cost four scenarios in a single eval run.

    The model answered "I still need to compare the book stock with a recount.
    If you want, I can do that comparison now" -- having called nothing. The
    manager had already asked for the comparison.
    """
    script(monkeypatch, [
        {"thought": "known", "answer": "We open at 19:00."},
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
        {"thought": "now I have it", "answer": "We open at 19:00."},
        PASS,
    ])
    r = loop.run("what time do we open")
    assert r.answer == "We open at 19:00."
    assert r.meta["pushbacks"] == 1
    assert r.meta["tools_called"] == 1


def test_a_lookup_on_its_own_is_not_a_record(monkeypatch):
    """resolve_product says the venue carries it. It says nothing else."""
    script(monkeypatch, [
        {"action": "resolve_product", "action_input": {"name": "Bombay Sapphire"}},
        {"answer": "I have resolved Bombay Sapphire to P009."},
        {"action": "get_inventory", "action_input": {"product_id": "P009"}},
        {"answer": "Bombay Sapphire books at 6.95 bottles."},
        PASS,
    ])
    r = loop.run("how much bombay sapphire")
    assert r.answer == "Bombay Sapphire books at 6.95 bottles."
    assert r.meta["pushbacks"] == 1


def test_an_offer_to_do_the_work_is_not_an_answer(monkeypatch):
    script(monkeypatch, [
        {"action": "get_inventory", "action_input": {"product_id": "P009"}},
        {"answer": "The book figure and the shelf disagree. If you want, I can "
                   "run that comparison now."},
        {"action": "reconcile", "action_input": {"product_id": "P009",
                                                 "physical_stock": 0}},
        {"answer": "The shelf is 6.95 short of the books."},
        PASS,
    ])
    r = loop.run("is the gin right")
    assert r.answer == "The shelf is 6.95 short of the books."
    assert r.meta["pushbacks"] == 1


def test_a_clarifying_question_is_left_alone(monkeypatch):
    """Asking which reading was meant is the required behaviour, not a punt."""
    script(monkeypatch, [
        {"action": "resolve_product", "action_input": {"name": "Bacardi"}},
        {"action": "get_inventory", "action_input": {"product_id": "P025"}},
        {"answer": "Is 12.5 what was poured tonight, or what is left on the "
                   "shelf? The two lead somewhere different."},
        PASS,
    ])
    r = loop.run("Bacardi Carta Blanca 12.5")
    assert "12.5" in r.answer
    assert r.meta["pushbacks"] == 0


def test_the_guard_stops_pushing_and_ships(monkeypatch):
    """Two attempts, then the answer goes out. A guard that never gives up is
    a timeout wearing a quality control badge."""
    punt = {"answer": "If you want, I can check that for you."}
    script(monkeypatch, [punt, punt, punt, PASS])
    r = loop.run("anything")
    assert r.answer == "If you want, I can check that for you."
    assert r.meta["pushbacks"] == loop.MAX_PUSHBACKS


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
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
        {"thought": "hmm"},
        {"thought": "right", "answer": "Sorted."},
        PASS,
    ])
    r = loop.run("anything")
    assert r.answer == "Sorted."
    assert "neither" in calls[2][1].lower()


def test_unparseable_json_from_the_model_does_not_kill_the_request(monkeypatch):
    """The model occasionally returns prose. That must become an observation
    the loop can recover from, not a 500 for the user."""
    script(monkeypatch, [
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
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
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
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
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
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
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
        {"answer": "A perfectly good answer."},
        RuntimeError("LLM call failed after 3 attempts"),
    ])
    r = loop.run("anything")
    assert r.answer == "A perfectly good answer."
    assert r.meta["review_passed"] is None


def test_the_result_carries_what_the_endpoint_returns(monkeypatch):
    script(monkeypatch, [
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
        {"answer": "fine"},
        PASS,
    ])
    r = loop.run("anything")
    assert r.answer == "fine"
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


def test_a_follow_up_carries_the_earlier_turn_into_the_prompt(monkeypatch):
    """The agent asks which reading was meant. The manager answers "poured".
    Without the earlier turn that reply is a single word about nothing."""
    calls = script(monkeypatch, [
        {"action": "get_inventory", "action_input": {"product_id": "P025"}},
        {"answer": "12.5 poured leaves 3.2 on the shelf."},
        PASS,
    ])
    loop.run("poured", history=[
        {"prompt": "Bacardi Carta Blanca 12.5",
         "answer": "Is 12.5 what was poured or what is left?"}])
    first = calls[0][1]
    assert "Bacardi Carta Blanca 12.5" in first
    assert "what was poured or what is left" in first
    assert "User question: poured" in first


def test_no_history_leaves_the_prompt_exactly_as_it_was(monkeypatch):
    """The brief fixes the input as a prompt and nothing else. A caller that
    sends only a prompt must get the behaviour it always got."""
    calls = script(monkeypatch, [
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
        {"answer": "done"},
        PASS,
    ])
    loop.run("how much carlsberg", history=[])
    assert calls[0][1] == "User question: how much carlsberg"


def test_only_the_last_few_turns_travel(monkeypatch):
    """Context costs money on a shared budget, and the turn before last is
    rarely what a follow-up refers to."""
    calls = script(monkeypatch, [
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
        {"answer": "done"},
        PASS,
    ])
    loop.run("and now", history=[{"prompt": f"question {i}", "answer": "a"}
                                 for i in range(6)])
    first = calls[0][1]
    assert "question 5" in first
    assert "question 0" not in first


def test_a_long_earlier_answer_is_trimmed(monkeypatch):
    calls = script(monkeypatch, [
        {"action": "get_inventory", "action_input": {"product_id": "K003"}},
        {"answer": "done"},
        PASS,
    ])
    loop.run("go on", history=[{"prompt": "q", "answer": "x" * 5000}])
    assert len(calls[0][1]) < 3000


def test_a_lookup_that_came_back_empty_is_left_to_stand(monkeypatch):
    """resolve_product("Macallan") returning found=false has answered the
    question. Sending that draft back looks for a record that cannot exist."""
    script(monkeypatch, [
        {"action": "resolve_product", "action_input": {"query": "Macallan"}},
        {"answer": "We do not carry Macallan, so there is no figure for it."},
        PASS,
    ])
    r = loop.run("how much macallan")
    assert r.meta["pushbacks"] == 0
    assert "Macallan" in r.answer


def test_a_clarifying_question_still_has_to_look_the_product_up(monkeypatch):
    """Observed live: "Bacardi Carta Blanca 12.5" answered with "could you
    clarify what you mean?" and nothing called. Asking which reading was meant
    is required; asking it without knowing the venue carries the bottle is the
    agent declining to look."""
    script(monkeypatch, [
        {"answer": "Could you clarify what you mean?"},
        {"action": "resolve_product", "action_input": {"query": "Bacardi Carta Blanca"}},
        {"action": "get_inventory", "action_input": {"product_id": "P025"}},
        {"answer": "Is 12.5 what was poured, or what is left on the shelf?"},
        PASS,
    ])
    r = loop.run("Bacardi Carta Blanca 12.5")
    assert r.meta["pushbacks"] == 1
    assert r.meta["tools_called"] == 2
    assert "12.5" in r.answer
