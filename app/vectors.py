"""Pinecone access.

REST rather than the pinecone SDK, for the same reason as Supabase: the SDK's
dependency tree costs cold-start time in a serverless function to perform one
HTTP request.

The passage text is stored as vector metadata, so a query returns the document
body with the hit. Going back to Postgres for the text would add a round trip
to every retrieval, and retrieval is on the critical path of most questions.
"""
import os

import requests

TIMEOUT = 30
NAMESPACE = "knowledge"


def configured():
    return bool(os.environ.get("PINECONE_API_KEY")
                and os.environ.get("PINECONE_INDEX_HOST"))


def require_pinecone():
    if not configured():
        raise RuntimeError(
            "PINECONE_API_KEY and PINECONE_INDEX_HOST are not set. "
            "Knowledge retrieval needs the vector index."
        )


def _host():
    return os.environ["PINECONE_INDEX_HOST"].replace("https://", "").rstrip("/")


def _headers():
    return {"Api-Key": os.environ["PINECONE_API_KEY"],
            "Content-Type": "application/json"}


def _post(path, payload):
    r = requests.post(f"https://{_host()}{path}", headers=_headers(),
                      json=payload, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def stats():
    require_pinecone()
    return _post("/describe_index_stats", {})


def count():
    namespaces = stats().get("namespaces") or {}
    if NAMESPACE in namespaces:
        return namespaces[NAMESPACE].get("vectorCount", 0)
    return stats().get("totalVectorCount", 0)


def upsert(vectors):
    """vectors: [{id, values, metadata}]"""
    require_pinecone()
    return _post("/vectors/upsert", {"vectors": vectors, "namespace": NAMESPACE})


def query(embedding, top_k=4):
    """Ranked passages for an already-embedded query.

    Returns doc_id, score, title and text together, so the caller needs no
    second lookup to quote the rule it is relying on.
    """
    require_pinecone()
    body = _post("/query", {
        "vector": embedding,
        "topK": top_k,
        "includeMetadata": True,
        "namespace": NAMESPACE,
    })
    return [{
        "doc_id": m.get("id"),
        "score": round(m.get("score", 0.0), 4),
        "title": (m.get("metadata") or {}).get("title", ""),
        "text": (m.get("metadata") or {}).get("text", ""),
    } for m in body.get("matches", [])]
