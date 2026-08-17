"""OpenSearch index for vacancy search — BM25 + knn_vector hybrid retrieval."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx


class OpenSearchVacancyIndexError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VacancyDocument:
    vacancy_id: str
    title: str
    company: str
    location: str
    description_text: str
    required_skills: tuple[str, ...]
    preferred_skills: tuple[str, ...]
    embedding: tuple[float, ...]
    embedding_model: str
    embedding_revision: str
    content_hash: str
    indexed_at: datetime


@dataclass(frozen=True, slots=True)
class VacancySearchHit:
    vacancy_id: str
    score: float


class OpenSearchVacancyIndex:
    """BM25 + knn_vector hybrid index for vacancy full-text search."""

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
        documents: tuple[VacancyDocument, ...],
        *,
        target_index: str | None = None,
    ) -> None:
        if not documents:
            return
        lines: list[str] = []
        for document in documents:
            if len(document.embedding) != self._dimensions:
                raise ValueError(
                    f"Embedding for {document.vacancy_id} has {len(document.embedding)} "
                    f"dimensions, expected {self._dimensions}"
                )
            lines.append(
                json.dumps(
                    {
                        "index": {
                            "_index": target_index or self._write_alias,
                            "_id": document.vacancy_id,
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
            raise OpenSearchVacancyIndexError("OpenSearch bulk index returned item errors")

    async def delete_document(self, vacancy_id: str) -> None:
        response = await self._http_client.post(
            f"/{self._write_alias}/_delete_by_query",
            json={"query": {"term": {"vacancy_id": vacancy_id}}},
        )
        self._require_success(response, operation="delete by vacancy_id")

    async def search_bm25(
        self,
        query_text: str,
        *,
        limit: int,
    ) -> tuple[VacancySearchHit, ...]:
        return await self._search(
            {
                "size": limit,
                "_source": False,
                "query": {
                    "multi_match": {
                        "query": query_text,
                        "fields": [
                            "title^3",
                            "company^2",
                            "required_skills^2",
                            "preferred_skills",
                            "location",
                            "description_text",
                        ],
                    }
                },
            }
        )

    async def search_knn(
        self,
        vector: tuple[float, ...],
        *,
        limit: int,
    ) -> tuple[VacancySearchHit, ...]:
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
                        }
                    }
                },
            }
        )

    async def search_rrf(
        self,
        query_text: str,
        vector: tuple[float, ...],
        *,
        bm25_limit: int,
        knn_limit: int,
        rrf_size: int,
        rrf_k: int = 60,
    ) -> tuple[VacancySearchHit, ...]:
        """Hybrid search using OpenSearch's native RRF sub_searches.

        This runs BM25 and knn as parallel sub-queries and fuses them with
        Reciprocal Rank Fusion in a single request — no separate round-trips.
        """
        return await self._search(
            {
                "size": rrf_size,
                "_source": False,
                "query": {
                    "rrf": {
                        "query": {
                            "multi_match": {
                                "query": query_text,
                                "fields": [
                                    "title^3",
                                    "company^2",
                                    "required_skills^2",
                                    "preferred_skills",
                                    "location",
                                    "description_text",
                                ],
                            }
                        },
                        "knn": {
                            "field": "embedding",
                            "query_vector": list(vector),
                            "k": knn_limit,
                        },
                        "rank_constant": rrf_k,
                        "rank_window_size": max(bm25_limit, knn_limit),
                    }
                },
            }
        )

    async def _search(self, query: dict[str, object]) -> tuple[VacancySearchHit, ...]:
        response = await self._http_client.post(f"/{self._read_alias}/_search", json=query)
        self._require_success(response, operation="search")
        hits = response.json().get("hits", {}).get("hits", [])
        return tuple(
            VacancySearchHit(vacancy_id=str(hit["_id"]), score=float(hit.get("_score") or 0))
            for hit in hits
        )

    def mapping(self) -> dict[str, object]:
        return {
            "settings": {"index": {"knn": True}},
            "mappings": {
                "dynamic": "strict",
                "properties": {
                    "vacancy_id": {"type": "keyword"},
                    "title": {"type": "text", "analyzer": "standard"},
                    "company": {"type": "keyword", "copy_to": "company_text"},
                    "company_text": {"type": "text"},
                    "location": {"type": "text"},
                    "description_text": {"type": "text", "analyzer": "standard"},
                    "required_skills": {"type": "keyword"},
                    "preferred_skills": {"type": "keyword"},
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

    @staticmethod
    def _document_payload(document: VacancyDocument) -> dict[str, object]:
        return {
            "vacancy_id": document.vacancy_id,
            "title": document.title,
            "company": document.company,
            "location": document.location,
            "description_text": document.description_text,
            "required_skills": list(document.required_skills),
            "preferred_skills": list(document.preferred_skills),
            "embedding": list(document.embedding),
            "embedding_model": document.embedding_model,
            "embedding_revision": document.embedding_revision,
            "content_hash": document.content_hash,
            "indexed_at": document.indexed_at.isoformat(),
        }

    @staticmethod
    def _require_success(response: httpx.Response, *, operation: str) -> None:
        if response.status_code >= 400:
            raise OpenSearchVacancyIndexError(
                f"OpenSearch {operation} failed with HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
