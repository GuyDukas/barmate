#!/usr/bin/env python3
"""
Embed the 14 operations documents and upsert them into Pinecone.

    python scripts/embed_knowledge.py           # embed and upload
    python scripts/embed_knowledge.py --check   # query the index, upload nothing

Reads the documents from Supabase, not from disk, so what gets embedded is
exactly what the agent can later read back. Embedding a local file that differs
from the seeded row would make retrieval quote text the database does not have.

Fourteen embedding calls at roughly 800 tokens each. Against a $13 group budget
this is not worth optimising, but it is worth not repeating: run it once after
seeding, and again only if the manual changes.
"""
import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_env():
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true",
                    help="run a sample query against the index, upload nothing")
    args = ap.parse_args()
    load_env()

    from app import db, llm, vectors

    if args.check:
        print(f"\n  {vectors.count()} vectors in namespace '{vectors.NAMESPACE}'\n")
        for question in ("why would happy hour lines show a stock gap",
                         "how do I convert bottles to millilitres",
                         "when does the beer supplier deliver"):
            print(f"  {question!r}")
            for hit in vectors.query(llm.embed(question), top_k=3):
                print(f"     {hit['score']:.3f}  {hit['doc_id']}  {hit['title']}")
            print()
        return 0

    docs = db.select("knowledge", order="doc_id.asc")
    if not docs:
        sys.exit("No rows in the knowledge table. Run scripts/seed_supabase.py first.")
    print(f"\n  embedding {len(docs)} documents")

    payload = []
    for doc in docs:
        text = doc["text"]
        # Title goes into the embedded string. Several documents describe
        # procedures without ever naming them, and a query phrased in the
        # title's words would otherwise miss the document it names.
        embedded = f"{doc['title']}\n\n{text}"
        payload.append({
            "id": doc["doc_id"],
            "values": llm.embed(embedded),
            "metadata": {"title": doc["title"], "text": text},
        })
        print(f"     {doc['doc_id']}  {doc['title'][:52]}")

    vectors.upsert(payload)
    print(f"\n  upserted {len(payload)} vectors, "
          f"{llm.usage['calls']} embedding calls\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
