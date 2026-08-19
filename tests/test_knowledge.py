import pytest

from app.tools import knowledge


def test_listing_is_available_without_an_embedding_call():
    """Asking what documents exist costs nothing. A similarity search costs an
    embedding call and a round trip to Pinecone. Which to spend is the agent's
    decision, so both doors have to be open."""
    r = knowledge.list_knowledge()
    assert len(r["documents"]) == 14
    assert all(d["title"] for d in r["documents"])
    assert all("text" not in d for d in r["documents"])


def test_a_document_can_be_fetched_by_id():
    r = knowledge.get_document("RAG-005")
    assert r["ok"] is True
    assert "Happy Hour" in r["title"]
    assert "18:00" in r["text"]


def test_an_unknown_document_is_an_error_not_an_empty_page():
    r = knowledge.get_document("RAG-999")
    assert r["ok"] is False
    assert "text" not in r


def test_search_refuses_rather_than_returning_no_passages():
    """Without the vector index a search returns nothing, and nothing reads as
    'the manual is silent on this'. That is a different and far worse answer
    than 'retrieval is unavailable', because the agent would go on to say the
    venue has no policy when it has one."""
    with pytest.raises(RuntimeError, match="vector index"):
        knowledge.search_knowledge("why is stock short during happy hour")
