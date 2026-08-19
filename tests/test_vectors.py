import pytest

from app import vectors


def test_retrieval_refuses_without_credentials():
    """Silently returning no passages would look like 'the manual says
    nothing', which is a different and much worse answer than an error."""
    with pytest.raises(RuntimeError, match="vector index"):
        vectors.require_pinecone()


def test_host_tolerates_a_pasted_https_prefix(monkeypatch):
    """The Pinecone console shows the host with a scheme; the API wants it
    without. Pasting it verbatim is the obvious mistake to absorb."""
    monkeypatch.setenv("PINECONE_INDEX_HOST", "https://idx-abc.svc.pinecone.io/")
    assert vectors._host() == "idx-abc.svc.pinecone.io"


def test_query_returns_text_with_the_hit(monkeypatch):
    """A hit carries its passage, so quoting the rule costs no second lookup."""
    monkeypatch.setenv("PINECONE_API_KEY", "k")
    monkeypatch.setenv("PINECONE_INDEX_HOST", "idx.pinecone.io")
    monkeypatch.setattr(vectors, "_post", lambda path, payload: {
        "matches": [{"id": "RAG-005", "score": 0.4301234,
                     "metadata": {"title": "Spillage, Happy Hour, and VIP Protocols",
                                  "text": "Happy hour runs 18:00 to 20:30."}}]
    })
    hits = vectors.query([0.0] * 1536, top_k=1)
    assert hits == [{
        "doc_id": "RAG-005",
        "score": 0.4301,
        "title": "Spillage, Happy Hour, and VIP Protocols",
        "text": "Happy hour runs 18:00 to 20:30.",
    }]


def test_missing_metadata_does_not_crash_retrieval(monkeypatch):
    monkeypatch.setenv("PINECONE_API_KEY", "k")
    monkeypatch.setenv("PINECONE_INDEX_HOST", "idx.pinecone.io")
    monkeypatch.setattr(vectors, "_post", lambda path, payload: {
        "matches": [{"id": "RAG-001", "score": 0.5}]
    })
    assert vectors.query([0.0] * 1536)[0]["text"] == ""
