"""Retrieval over the operations manual.

Three entry points on purpose, with different costs. `list_knowledge` and
`get_document` read Postgres and cost nothing extra, which is the right call
when the agent already knows it wants the happy-hour rules. `search_knowledge`
costs one embedding call plus a query against Pinecone, and earns that when the
question does not name a document.

Search never degrades to silence. With no vector index reachable it raises,
because an empty result list reads as "the manual says nothing about this" and
the agent would go on to report that the venue has no policy when it has one.
"""
from app import db, llm, vectors


def list_knowledge():
    docs = db.select("knowledge", columns="doc_id,title", order="doc_id.asc")
    return {"ok": True, "count": len(docs), "documents": [
        {"doc_id": d["doc_id"], "title": d["title"]} for d in docs]}


def get_document(doc_id):
    rows = db.select("knowledge", doc_id=doc_id)
    if not rows:
        available = [d["doc_id"] for d in list_knowledge()["documents"]]
        return {"ok": False, "doc_id": doc_id,
                "error": f"{doc_id} is not one of the operations documents",
                "available": available}
    d = rows[0]
    return {"ok": True, "doc_id": d["doc_id"], "title": d["title"],
            "text": d["text"]}


def search_knowledge(query, top_k=3):
    """Ranked passages for a question, by meaning rather than keyword.

    The passage text rides along with the hit, so quoting the rule the answer
    rests on costs no second lookup.
    """
    vectors.require_pinecone()
    hits = vectors.query(llm.embed(query), top_k=top_k)
    return {
        "ok": True,
        "query": query,
        "count": len(hits),
        "documents": hits,
        "note": ("Ranked by embedding similarity over the 14 operations "
                 "documents. A low top score means the manual does not cover "
                 "the question, not that the answer is the closest match."),
    }
