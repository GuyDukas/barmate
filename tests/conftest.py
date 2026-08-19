import pytest


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    """Force every test onto the offline bundle.

    Without this, a developer with credentials exported in their shell runs a
    different suite from CI: the tools would query Supabase, tests would depend
    on network and on whatever happens to be seeded, and a failure would be
    ambiguous between a code bug and a stale database. Tests that want the
    database ask for it explicitly.
    """
    for name in ("SUPABASE_URL", "SUPABASE_SERVICE_KEY",
                 "PINECONE_API_KEY", "PINECONE_INDEX_HOST", "LLMOD_API_KEY"):
        monkeypatch.delenv(name, raising=False)
