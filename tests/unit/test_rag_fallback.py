"""RAG fallback client unit tests."""

from __future__ import annotations

import asyncio

from app.matching.rag_client import RagFallbackClient


def test_fallback_client_returns_empty():
    """RagFallbackClient returns empty results without network calls."""
    client = RagFallbackClient()

    result = asyncio.run(client.search("test", owner_user_id="x"))
    assert result.results == ()
    assert result.degraded is True
    assert result.effective_mode == "fallback"


def test_fallback_health_reports_unavailable():
    client = RagFallbackClient()
    health = asyncio.run(client.health())
    assert health.available is False
    assert health.status == "not_configured"


def test_fallback_delete_returns_false():
    client = RagFallbackClient()
    deleted = asyncio.run(
        client.delete_document(owner_user_id="x", external_document_id="y", collection="z")
    )
    assert deleted is False


def test_fallback_ingest_returns_skipped():
    client = RagFallbackClient()
    result = asyncio.run(
        client.ingest_document(
            owner_user_id="x",
            external_document_id="doc-1",
            content="content",
            collection="default",
        )
    )
    assert result.status == "skipped"
    assert result.document_id == ""
