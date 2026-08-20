from app.tools import registry


def test_every_tool_has_a_schema():
    assert set(registry.TOOLS) == set(registry.SCHEMAS)


def test_the_prompt_catalogue_lists_every_tool():
    """The model can only call what the prompt tells it about. A tool present
    in the registry but missing from the catalogue is dead code the agent will
    never reach."""
    catalogue = registry.catalogue_for_prompt()
    for name in registry.TOOLS:
        assert name in catalogue


def test_unknown_tool_returns_an_error_not_an_exception():
    r = registry.run_tool("delete_everything", {})
    assert r["ok"] is False
    assert "unknown tool" in r["error"].lower()
    assert "get_inventory" in r["error"]


def test_bad_arguments_return_an_error_not_a_crash():
    r = registry.run_tool("get_inventory", {"wrong_arg": 1})
    assert r["ok"] is False
    assert "wrong_arg" in r["error"]


def test_a_missing_required_argument_is_an_observation():
    r = registry.run_tool("get_inventory", {})
    assert r["ok"] is False


def test_arguments_must_be_an_object():
    r = registry.run_tool("get_inventory", ["P019"])
    assert r["ok"] is False


def test_tool_call_succeeds():
    r = registry.run_tool("get_inventory", {"product_id": "P019"})
    assert r["ok"] is True
    assert r["name"] == "Jameson"


def test_a_raising_tool_comes_back_as_an_observation():
    """Knowledge search raises without a vector index, by design. Inside the
    ReAct loop that exception would end the request; as an observation the
    agent can say retrieval is unavailable and carry on with what it has."""
    r = registry.run_tool("search_knowledge", {"query": "happy hour"})
    assert r["ok"] is False
    assert "vector index" in r["error"]


def test_internal_plumbing_arguments_are_not_offered_to_the_model():
    """Some tools take a prefetched-rows argument so two calls can share one
    fetch. That is an implementation detail; letting the model pass it invites
    a crash and tells it nothing useful."""
    r = registry.run_tool("get_inventory", {"product_id": "P019", "_mv": None})
    assert r["ok"] is False
    assert "_mv" in r["error"]
    assert "_mv" not in registry.SCHEMAS["get_inventory"]


def test_no_tool_can_place_an_order_or_change_a_record():
    """GT005 turns on BarMate having no way to transmit an order. That has to
    be a fact about the registry, not a promise in the prompt, because a
    prompt can be talked around and a missing tool cannot."""
    forbidden = ("send", "submit", "place", "order_", "write", "update",
                 "delete", "insert", "post", "email", "mutate")
    for name in registry.TOOLS:
        assert not any(word in name.lower() for word in forbidden), name


def test_read_only_is_stated_in_the_catalogue():
    assert "cannot" in registry.catalogue_for_prompt().lower()


def test_every_tool_is_a_function_and_every_one_has_a_schema():
    """A schema string once landed in TOOLS instead of SCHEMAS, because the
    edit anchored on a prefix that appears in both tables. The dispatch table
    held a tool whose implementation was its own description."""
    assert all(callable(fn) for fn in registry.TOOLS.values())
    assert set(registry.TOOLS) == set(registry.SCHEMAS)
