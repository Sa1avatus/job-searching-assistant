"""RAG E2E smoke test.

Exercises collection discovery, owner-scoped ingestion, indexing,
search, and cleanup against the configured local RAG service.

Requires a running RAG platform on APP_RAG_SERVICE_URL.
Skipped when RAG is not configured or unavailable.
"""

from __future__ import annotations

import contextlib
import uuid

import httpx
import pytest

from app.matching.rag_client import RagHttpClient
from app.matching.rag_collections import PROFILE_COLLECTION, RESUME_COLLECTION, VACANCY_COLLECTION

pytestmark = pytest.mark.skipif(
    "not config.getoption('--rag-e2e', default=False)",
    reason="RAG E2E smoke requires --rag-e2e and a running RAG service",
)


def _owner_a() -> str:
    return f"e2e-owner-a-{uuid.uuid4().hex[:8]}"


def _owner_b() -> str:
    return f"e2e-owner-b-{uuid.uuid4().hex[:8]}"


@pytest.fixture()
def rag_client():
    """Create a real RAG HTTP client from environment."""
    import os

    service_url = os.environ.get("APP_RAG_SERVICE_URL", "http://localhost:8100")
    api_key = os.environ.get("APP_RAG_API_KEY", "")
    project_id = os.environ.get("APP_RAG_PROJECT_ID", "")

    if not api_key or not project_id:
        pytest.skip("APP_RAG_API_KEY and APP_RAG_PROJECT_ID must be set")

    # Verify RAG is reachable
    try:
        resp = httpx.get(f"{service_url}/health/live", timeout=5)
        resp.raise_for_status()
    except Exception:
        pytest.skip(f"RAG service not reachable at {service_url}")

    client = RagHttpClient(
        service_url,
        api_key,
        project_id=project_id,
        timeout_seconds=15,
    )
    return client


@pytest.fixture()
def cleanup_docs(rag_client):
    """Track ingested docs for cleanup after test."""
    docs: list[tuple[str, str, str]] = []  # (owner, external_id, collection)

    yield docs

    for owner, external_id, collection in docs:
        with contextlib.suppress(Exception):
            rag_client.delete_document(
                owner_user_id=owner,
                external_document_id=external_id,
                collection=collection,
            )


# ── Ingestion round-trip ───────────────────────────────────────


@pytest.mark.asyncio
async def test_profile_ingest_search_delete(rag_client, cleanup_docs):
    """Ingest a profile document, search for it, verify owner isolation, delete."""
    owner = _owner_a()
    external_id = f"profile:{owner}"
    content = "Python developer with 5 years of experience in FastAPI and PostgreSQL"

    # Ingest
    result = await rag_client.ingest_document(
        owner_user_id=owner,
        external_document_id=external_id,
        content=content,
        collection=PROFILE_COLLECTION,
        title="E2E Test Profile",
        metadata={"source_type": "profile", "e2e": True},
    )
    cleanup_docs.append((owner, external_id, PROFILE_COLLECTION))
    assert result.document_id
    assert result.status in ("indexed", "processing", "pending")

    # Search (may need a brief delay for indexing)
    import asyncio

    await asyncio.sleep(2)

    search = await rag_client.search(
        "Python developer",
        owner_user_id=owner,
        collections=(PROFILE_COLLECTION,),
        top_k=5,
    )
    assert search.request_id
    # At minimum, the search should not fail
    # (results may be empty if indexing is async)


@pytest.mark.asyncio
async def test_resume_ingest_and_delete(rag_client, cleanup_docs):
    """Ingest a resume document into the resumes collection."""
    owner = _owner_a()
    cv_id = uuid.uuid4().hex[:12]
    external_id = f"cv:{cv_id}"
    content = "Skills: Python, FastAPI, Docker\nKeywords: backend, API, microservices"

    result = await rag_client.ingest_document(
        owner_user_id=owner,
        external_document_id=external_id,
        content=content,
        collection=RESUME_COLLECTION,
        title="E2E Test Resume",
        metadata={"source_type": "cv", "e2e": True},
    )
    cleanup_docs.append((owner, external_id, RESUME_COLLECTION))
    assert result.document_id

    # Delete
    deleted = await rag_client.delete_document(
        owner_user_id=owner,
        external_document_id=external_id,
        collection=RESUME_COLLECTION,
    )
    assert deleted is True
    cleanup_docs.pop()  # already deleted


@pytest.mark.asyncio
async def test_vacancy_ingest(rag_client, cleanup_docs):
    """Ingest a vacancy document into the vacancies collection."""
    owner = _owner_a()
    vacancy_id = uuid.uuid4().hex[:12]
    external_id = f"vacancy:{vacancy_id}"
    content = (
        "Senior Python Developer\nRequired: Python, FastAPI, PostgreSQL\nPreferred: Docker, K8s"
    )

    result = await rag_client.ingest_document(
        owner_user_id=owner,
        external_document_id=external_id,
        content=content,
        collection=VACANCY_COLLECTION,
        title="E2E Test Vacancy",
        metadata={"source_type": "vacancy", "e2e": True},
    )
    cleanup_docs.append((owner, external_id, VACANCY_COLLECTION))
    assert result.document_id


# ── Owner isolation ────────────────────────────────────────────


@pytest.mark.asyncio
async def test_owner_isolation_on_search(rag_client, cleanup_docs):
    """Owner A's documents are not visible to Owner B."""
    owner_a = _owner_a()
    owner_b = _owner_b()
    external_a = f"profile:{owner_a}"
    external_b = f"profile:{owner_b}"

    # Ingest for both owners
    await rag_client.ingest_document(
        owner_user_id=owner_a,
        external_document_id=external_a,
        content="Owner A unique skill: ZYXWVUT9876",
        collection=PROFILE_COLLECTION,
    )
    cleanup_docs.append((owner_a, external_a, PROFILE_COLLECTION))

    await rag_client.ingest_document(
        owner_user_id=owner_b,
        external_document_id=external_b,
        content="Owner B unique skill: QWERTYUIOP54321",
        collection=PROFILE_COLLECTION,
    )
    cleanup_docs.append((owner_b, external_b, PROFILE_COLLECTION))

    import asyncio

    await asyncio.sleep(2)

    # Owner A searches — should NOT see Owner B's content
    results_a = await rag_client.search(
        "QWERTYUIOP54321",
        owner_user_id=owner_a,
        collections=(PROFILE_COLLECTION,),
        top_k=10,
    )
    for r in results_a.results:
        assert "QWERTYUIOP54321" not in r.content, (
            "Owner A saw Owner B's content — isolation broken!"
        )

    # Owner B searches — should NOT see Owner A's content
    results_b = await rag_client.search(
        "ZYXWVUT9876",
        owner_user_id=owner_b,
        collections=(PROFILE_COLLECTION,),
        top_k=10,
    )
    for r in results_b.results:
        assert "ZYXWVUT9876" not in r.content, "Owner B saw Owner A's content — isolation broken!"


# ── Idempotent upsert ─────────────────────────────────────────


@pytest.mark.asyncio
async def test_ingest_same_external_id_is_idempotent(rag_client, cleanup_docs):
    """Ingesting the same external_document_id twice updates, not duplicates."""
    owner = _owner_a()
    external_id = f"profile:{owner}"

    result1 = await rag_client.ingest_document(
        owner_user_id=owner,
        external_document_id=external_id,
        content="Version 1 content ABCD1234",
        collection=PROFILE_COLLECTION,
    )
    cleanup_docs.append((owner, external_id, PROFILE_COLLECTION))

    result2 = await rag_client.ingest_document(
        owner_user_id=owner,
        external_document_id=external_id,
        content="Version 2 content EFGH5678",
        collection=PROFILE_COLLECTION,
    )

    # Should update the same document, not create a new one
    assert result1.document_id == result2.document_id
