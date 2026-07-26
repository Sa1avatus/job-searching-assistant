from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx


class OpenSearchIndexError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class EvidenceDocument:
    evidence_id: str
    user_id: str
    cv_file_id: str
    evidence_text: str
    normalized_text: str
    skill_name: str | None
    evidence_type: str
    experience_level: str
    is_verified: bool
    years: float | None
    confidence: float
    embedding: tuple[float, ...]
    embedding_model: str
    embedding_revision: str
    content_hash: str
    indexed_at: datetime


@dataclass(frozen=True, slots=True)
class SearchHit:
    evidence_id: str
    score: float


class OpenSearchEvidenceIndex:
    def __init__(
        self,
        http_client: httpx.AsyncClient,
        *,
        index_prefix: str,
        read_alias: str,
        write_alias: str,
        dimensions: int,
    ) -> None:
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self._http_client = http_client
        self._index_prefix = index_prefix
        self._read_alias = read_alias
        self._write_alias = write_alias
        self._dimensions = dimensions

    def versioned_index_name(self, *, created_at: datetime | None = None) -> str:
        timestamp = (created_at or datetime.now(UTC)).strftime("%Y%m%d%H%M%S")
        return f"{self._index_prefix}-v1-{timestamp}"

    async def ensure_index(self) -> str | None:
        response = await self._http_client.get(f"/_alias/{self._write_alias}")
        if response.status_code == 200:
            aliases = response.json()
            return next(iter(aliases), None)
        if response.status_code != 404:
            self._require_success(response, operation="inspect write alias")
        index_name = self.versioned_index_name()
        await self.create_index(index_name)
        await self.switch_aliases(index_name)
        return index_name

    async def create_index(self, index_name: str) -> None:
        response = await self._http_client.put(f"/{index_name}", json=self.mapping())
        self._require_success(response, operation="create index")

    async def switch_aliases(self, index_name: str) -> None:
        response = await self._http_client.post(
            "/_aliases",
            json={
                "actions": [
                    {"remove": {"index": f"{self._index_prefix}-v1-*", "alias": self._read_alias}},
                    {"remove": {"index": f"{self._index_prefix}-v1-*", "alias": self._write_alias}},
                    {"add": {"index": index_name, "alias": self._read_alias}},
                    {
                        "add": {
                            "index": index_name,
                            "alias": self._write_alias,
                            "is_write_index": True,
                        }
                    },
                ]
            },
        )
        self._require_success(response, operation="switch aliases")

    async def index_documents(
        self,
        documents: tuple[EvidenceDocument, ...],
        *,
        target_index: str | None = None,
    ) -> None:
        if not documents:
            return
        lines: list[str] = []
        for document in documents:
            if len(document.embedding) != self._dimensions:
                raise ValueError(
                    f"Embedding for {document.evidence_id} has {len(document.embedding)} "
                    f"dimensions, expected {self._dimensions}"
                )
            lines.append(
                json.dumps(
                    {
                        "index": {
                            "_index": target_index or self._write_alias,
                            "_id": document.evidence_id,
                        }
                    },
                    separators=(",", ":"),
                )
            )
            lines.append(json.dumps(self._document_payload(document), separators=(",", ":")))
        response = await self._http_client.post(
            "/_bulk",
            content="\n".join(lines) + "\n",
            headers={"content-type": "application/x-ndjson"},
        )
        self._require_success(response, operation="bulk index")
        payload = response.json()
        if payload.get("errors"):
            raise OpenSearchIndexError("OpenSearch bulk index returned item errors")

    async def search_bm25(
        self,
        query_text: str,
        *,
        user_id: str,
        cv_file_id: str,
        limit: int,
    ) -> tuple[SearchHit, ...]:
        return await self._search(
            {
                "size": limit,
                "_source": False,
                "query": {
                    "bool": {
                        "must": [
                            {
                                "multi_match": {
                                    "query": query_text,
                                    "fields": [
                                        "evidence_text^2",
                                        "normalized_text",
                                        "skill_name^3",
                                    ],
                                }
                            }
                        ],
                        "filter": self._tenant_filters(user_id, cv_file_id),
                    }
                },
            }
        )

    async def search_knn(
        self,
        vector: tuple[float, ...],
        *,
        user_id: str,
        cv_file_id: str,
        limit: int,
    ) -> tuple[SearchHit, ...]:
        if len(vector) != self._dimensions:
            raise ValueError("Query embedding dimensions do not match the index")
        return await self._search(
            {
                "size": limit,
                "_source": False,
                "query": {
                    "knn": {
                        "embedding": {
                            "vector": list(vector),
                            "k": limit,
                            "filter": {
                                "bool": {
                                    "filter": self._tenant_filters(user_id, cv_file_id),
                                }
                            },
                        }
                    }
                },
            }
        )

    async def delete_index(self, index_name: str) -> None:
        response = await self._http_client.delete(f"/{index_name}")
        self._require_success(response, operation="delete index")

    def mapping(self) -> dict[str, object]:
        return {
            "settings": {"index": {"knn": True}},
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "evidence_id": {"type": "keyword"},
                    "user_id": {"type": "keyword"},
                    "cv_file_id": {"type": "keyword"},
                    "evidence_text": {"type": "text"},
                    "normalized_text": {"type": "text"},
                    "skill_name": {"type": "keyword"},
                    "evidence_type": {"type": "keyword"},
                    "experience_level": {"type": "keyword"},
                    "is_verified": {"type": "boolean"},
                    "years": {"type": "float"},
                    "confidence": {"type": "float"},
                    "embedding": {
                        "type": "knn_vector",
                        "dimension": self._dimensions,
                        "method": {
                            "name": "hnsw",
                            "space_type": "cosinesimil",
                            "engine": "lucene",
                        },
                    },
                    "embedding_model": {"type": "keyword"},
                    "embedding_revision": {"type": "keyword"},
                    "content_hash": {"type": "keyword"},
                    "indexed_at": {"type": "date"},
                },
            },
        }

    async def _search(self, query: dict[str, object]) -> tuple[SearchHit, ...]:
        response = await self._http_client.post(f"/{self._read_alias}/_search", json=query)
        self._require_success(response, operation="search")
        hits = response.json().get("hits", {}).get("hits", [])
        return tuple(
            SearchHit(evidence_id=str(hit["_id"]), score=float(hit.get("_score") or 0))
            for hit in hits
        )

    @staticmethod
    def _tenant_filters(user_id: str, cv_file_id: str) -> list[dict[str, object]]:
        return [
            {"term": {"user_id": user_id}},
            {"term": {"cv_file_id": cv_file_id}},
            {"term": {"is_verified": True}},
        ]

    @staticmethod
    def _document_payload(document: EvidenceDocument) -> dict[str, object]:
        return {
            "evidence_id": document.evidence_id,
            "user_id": document.user_id,
            "cv_file_id": document.cv_file_id,
            "evidence_text": document.evidence_text,
            "normalized_text": document.normalized_text,
            "skill_name": document.skill_name,
            "evidence_type": document.evidence_type,
            "experience_level": document.experience_level,
            "is_verified": document.is_verified,
            "years": document.years,
            "confidence": document.confidence,
            "embedding": list(document.embedding),
            "embedding_model": document.embedding_model,
            "embedding_revision": document.embedding_revision,
            "content_hash": document.content_hash,
            "indexed_at": document.indexed_at.isoformat(),
        }

    @staticmethod
    def _require_success(response: httpx.Response, *, operation: str) -> None:
        if response.status_code >= 400:
            raise OpenSearchIndexError(
                f"OpenSearch {operation} failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
