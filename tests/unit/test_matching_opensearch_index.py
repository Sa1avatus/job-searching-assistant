import asyncio
import json
from datetime import UTC, datetime

import httpx
import pytest

from app.matching.opensearch_index import (
    EvidenceDocument,
    OpenSearchEvidenceIndex,
    OpenSearchIndexError,
)


def _document() -> EvidenceDocument:
    return EvidenceDocument(
        evidence_id="evidence-1",
        user_id="user-1",
        cv_file_id="cv-1",
        evidence_text="Built Python services",
        normalized_text="python services",
        skill_name="Python",
        evidence_type="work_experience",
        experience_level="production",
        is_verified=True,
        years=3,
        confidence=0.95,
        embedding=(1.0, 0.0, 0.0),
        embedding_model="fake",
        embedding_revision="1",
        content_hash="a" * 64,
        indexed_at=datetime(2026, 7, 26, tzinfo=UTC),
    )


def test_index_mapping_and_bulk_payload_are_versioned_and_tenant_scoped() -> None:
    async def run() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path.endswith("_search"):
                return httpx.Response(
                    200,
                    json={"hits": {"hits": [{"_id": "evidence-1", "_score": 2.5}]}},
                )
            return httpx.Response(200, json={"errors": False})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://opensearch.test",
        ) as client:
            index = OpenSearchEvidenceIndex(
                client,
                index_prefix="candidate-evidence",
                read_alias="candidate-evidence-read",
                write_alias="candidate-evidence-write",
                dimensions=3,
            )
            assert (
                index.versioned_index_name(created_at=datetime(2026, 7, 26, 1, 2, 3, tzinfo=UTC))
                == "candidate-evidence-v1-20260726010203"
            )
            assert (
                index.mapping()["mappings"]["properties"]["embedding"]["dimension"] == 3  # type: ignore[index]
            )
            await index.index_documents((_document(),))
            hits = await index.search_bm25(
                "Python",
                user_id="user-1",
                cv_file_id="cv-1",
                limit=5,
            )

        bulk_request = next(request for request in requests if request.url.path == "/_bulk")
        bulk_lines = bulk_request.content.decode().splitlines()
        assert json.loads(bulk_lines[0])["index"]["_index"] == "candidate-evidence-write"
        assert json.loads(bulk_lines[1])["user_id"] == "user-1"
        search_request = next(
            request for request in requests if request.url.path.endswith("_search")
        )
        search_body = json.loads(search_request.content)
        assert {"term": {"user_id": "user-1"}} in search_body["query"]["bool"]["filter"]
        assert {"term": {"cv_file_id": "cv-1"}} in search_body["query"]["bool"]["filter"]
        assert hits[0].evidence_id == "evidence-1"

    asyncio.run(run())


def test_index_rejects_embedding_dimension_mismatch_before_network() -> None:
    async def run() -> None:
        async with httpx.AsyncClient(base_url="http://opensearch.test") as client:
            index = OpenSearchEvidenceIndex(
                client,
                index_prefix="candidate-evidence",
                read_alias="candidate-evidence-read",
                write_alias="candidate-evidence-write",
                dimensions=2,
            )
            with pytest.raises(ValueError, match="expected 2"):
                await index.index_documents((_document(),))

    asyncio.run(run())


def test_ensure_index_bootstraps_missing_write_alias() -> None:
    async def run() -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(404, json={"error": "alias missing"})
            return httpx.Response(200, json={"acknowledged": True})

        async with httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            base_url="http://opensearch.test",
        ) as client:
            index = OpenSearchEvidenceIndex(
                client,
                index_prefix="candidate-evidence",
                read_alias="candidate-evidence-read",
                write_alias="candidate-evidence-write",
                dimensions=3,
            )
            index_name = await index.ensure_index()

        assert index_name is not None
        assert requests[0].url.path == "/_alias/candidate-evidence-write"
        assert any(request.method == "PUT" for request in requests)
        assert any(request.url.path == "/_aliases" for request in requests)

    asyncio.run(run())


def test_index_surfaces_bounded_provider_error() -> None:
    async def run() -> None:
        transport = httpx.MockTransport(
            lambda request: httpx.Response(503, text="temporarily unavailable")
        )
        async with httpx.AsyncClient(
            transport=transport,
            base_url="http://opensearch.test",
        ) as client:
            index = OpenSearchEvidenceIndex(
                client,
                index_prefix="candidate-evidence",
                read_alias="candidate-evidence-read",
                write_alias="candidate-evidence-write",
                dimensions=3,
            )
            with pytest.raises(OpenSearchIndexError, match="HTTP 503"):
                await index.create_index("candidate-evidence-v1-test")

    asyncio.run(run())
