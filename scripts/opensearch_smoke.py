from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime

import httpx

from app.config import get_settings
from app.matching.opensearch_index import EvidenceDocument, OpenSearchEvidenceIndex


async def main() -> None:
    settings = get_settings()
    async with httpx.AsyncClient(base_url=settings.opensearch_url, timeout=30) as client:
        evidence_index = OpenSearchEvidenceIndex(
            client,
            index_prefix=settings.opensearch_evidence_index_prefix,
            read_alias=settings.opensearch_evidence_read_alias,
            write_alias=settings.opensearch_evidence_write_alias,
            dimensions=settings.embedding_dimensions,
        )
        index_name = evidence_index.versioned_index_name()
        await evidence_index.create_index(index_name)
        try:
            await evidence_index.switch_aliases(index_name)
            vector = (1.0,) + (0.0,) * (settings.embedding_dimensions - 1)
            evidence_text = "Built production Python services"
            await evidence_index.index_documents(
                (
                    EvidenceDocument(
                        evidence_id="opensearch-smoke-evidence",
                        user_id="opensearch-smoke-user",
                        cv_file_id="opensearch-smoke-cv",
                        evidence_text=evidence_text,
                        normalized_text="python production services",
                        skill_name="Python",
                        evidence_type="work_experience",
                        experience_level="production",
                        is_verified=True,
                        years=3,
                        confidence=1,
                        embedding=vector,
                        embedding_model="smoke",
                        embedding_revision="1",
                        content_hash=hashlib.sha256(evidence_text.encode()).hexdigest(),
                        indexed_at=datetime.now(UTC),
                    ),
                )
            )
            await client.post(f"/{settings.opensearch_evidence_write_alias}/_refresh")
            hits = await evidence_index.search_bm25(
                "Python production",
                user_id="opensearch-smoke-user",
                cv_file_id="opensearch-smoke-cv",
                limit=5,
            )
            if not hits or hits[0].evidence_id != "opensearch-smoke-evidence":
                raise RuntimeError("OpenSearch smoke evidence was not retrieved")
            print(f"OpenSearch smoke passed using {index_name}")
        finally:
            await evidence_index.delete_index(index_name)


if __name__ == "__main__":
    asyncio.run(main())
