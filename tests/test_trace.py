import pytest

from app.agent.trace import Trace


def test_step_shape_matches_the_brief():
    t = Trace()
    t.add("Reasoner", "sys", "usr", {"thought": "x"})
    step = t.as_list()[0]
    assert set(step) == {"module", "prompt", "response"}
    assert set(step["prompt"]) == {"System_prompt", "User_prompt"}


def test_module_names_are_constrained_to_the_diagram():
    """A guard against drift. If someone adds a module without updating the
    architecture image, the tests fail rather than the grader noticing."""
    with pytest.raises(ValueError, match="architecture"):
        Trace().add("Router", "s", "u", {})


def test_an_observation_rides_with_the_step_that_produced_it():
    """The trace is the evidence that the answer came from data. A step that
    shows the model deciding to call get_inventory, without showing what came
    back, proves nothing."""
    t = Trace()
    t.add("Reasoner", "s", "u", {"action": "get_inventory"},
          observation={"ok": True, "book_stock": 6.08})
    step = t.as_list()[0]
    assert step["response"]["observation"]["book_stock"] == 6.08
    assert set(step) == {"module", "prompt", "response"}


def test_observations_are_kept_apart_for_the_reviewer():
    """The reviewer checks that every number in the draft came from a tool. It
    has to be handed the tool results, not the model's own account of them,
    or it will confirm whatever the model made up."""
    t = Trace()
    t.add("Reasoner", "s", "u", {"thought": "I think there are 40 kegs"})
    t.add("Reasoner", "s", "u", {"action": "get_inventory"},
          observation={"ok": True, "book_stock": 6.08})
    assert t.observations == [{"ok": True, "book_stock": 6.08}]
