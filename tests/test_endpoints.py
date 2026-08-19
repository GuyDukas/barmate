from api.index import app


def client():
    app.config["TESTING"] = True
    return app.test_client()


def test_team_info_shape():
    d = client().get("/api/team_info").get_json()
    assert set(d) >= {"group_batch_order_number", "team_name", "students"}
    assert len(d["students"]) == 3
    assert all({"name", "email"} <= set(s) for s in d["students"])


def test_team_info_has_no_placeholders_left():
    """The endpoint is graded on returning real student details. This fails
    until the emails and the batch order number are filled in, so an
    unfinished team_info cannot reach submission unnoticed."""
    d = client().get("/api/team_info").get_json()
    unfilled = [s["name"] for s in d["students"] if s["email"] == "TBD"]
    assert not unfilled, f"email still TBD for: {', '.join(unfilled)}"
    assert "TBD" not in d["group_batch_order_number"]


def test_agent_info_includes_worked_examples():
    d = client().get("/api/agent_info").get_json()
    assert d["prompt_examples"]
    assert "steps" in d["prompt_examples"][0]
    assert d["prompt_examples"][0]["steps"]


def test_agent_info_module_names_match_the_architecture():
    """The brief requires module names to be consistent across the diagram,
    the steps trace and the descriptions. This pins the trace side."""
    d = client().get("/api/agent_info").get_json()
    modules = {s["module"] for e in d["prompt_examples"] for s in e["steps"]}
    assert modules <= {"Reasoner", "KnowledgeRetriever", "Reflector", "Reviser"}


def test_architecture_is_a_png():
    r = client().get("/api/model_architecture")
    assert r.headers["Content-Type"] == "image/png"
    assert r.data[:8] == b"\x89PNG\r\n\x1a\n"


def test_execute_rejects_an_empty_prompt_cleanly():
    d = client().post("/api/execute", json={"prompt": ""}).get_json()
    assert d["status"] == "error"
    assert d["response"] is None
    assert d["steps"] == []


def test_execute_always_returns_the_four_top_level_fields():
    """The brief fixes this envelope. It must hold on the error path too,
    which is where a hand-written error response usually drifts."""
    for body in ({"prompt": ""}, {}, {"prompt": "how much gin"}):
        d = client().post("/api/execute", json=body).get_json()
        assert set(d) == {"status", "error", "response", "steps"}
        assert d["status"] in ("ok", "error")


def test_gui_is_served_without_auth():
    r = client().get("/")
    assert r.status_code == 200
    assert b"Run Agent" in r.data
