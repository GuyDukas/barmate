import json

import pytest

from app import llm


def reply(text, monkeypatch):
    monkeypatch.setattr(llm, "_post", lambda path, payload: {
        "choices": [{"message": {"content": text}}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1},
    })


def test_plain_json_parses(monkeypatch):
    reply('{"answer": "yes"}', monkeypatch)
    assert llm.chat("s", "u") == {"answer": "yes"}


def test_a_fenced_block_parses(monkeypatch):
    reply('```json\n{"answer": "yes"}\n```', monkeypatch)
    assert llm.chat("s", "u") == {"answer": "yes"}


def test_a_repeated_object_is_read_once(monkeypatch):
    """Observed from MB5R2CF-azure/gpt-5.4-mini in JSON mode: it sometimes
    emits the same object twice, back to back. json.loads rejects the pair as
    'Extra data', so a perfectly good tool call was being thrown away and the
    agent burned its whole iteration budget without calling anything."""
    one = '{"thought":"resolve first","action":"resolve_product","action_input":{"query":"Macallan"}}'
    reply(one + "\n" + one, monkeypatch)
    assert llm.chat("s", "u")["action"] == "resolve_product"


def test_trailing_prose_after_the_object_is_ignored(monkeypatch):
    reply('{"answer": "yes"}\n\nHope that helps!', monkeypatch)
    assert llm.chat("s", "u") == {"answer": "yes"}


def test_leading_prose_is_recovered(monkeypatch):
    reply('Sure thing.\n{"answer": "yes"}', monkeypatch)
    assert llm.chat("s", "u") == {"answer": "yes"}


def test_genuinely_unparseable_still_raises(monkeypatch):
    """The loop recovers by handing the failure back to the model as an
    observation, so this must stay an error rather than become a silent {}."""
    reply("I am afraid I cannot do that.", monkeypatch)
    with pytest.raises(json.JSONDecodeError):
        llm.chat("s", "u")


def test_token_usage_accumulates(monkeypatch):
    reply('{"answer": "yes"}', monkeypatch)
    before = llm.usage["calls"]
    llm.chat("s", "u")
    assert llm.usage["calls"] == before + 1
