import asyncio

import httpx
import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings
from app.matching.shadow_scorer import compute_shadow_score
from app.storage.database import Base
from app.storage.tables import MatchingEmbeddingCacheRow


def _session_factory():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)


def _settings() -> Settings:
    return Settings(
        matching_scorer="shadow",
        embedding_service_url="http://embeddings.test",
        matching_scorer_embedding_model_name="intfloat/multilingual-e5-small",
        matching_scorer_embedding_timeout_seconds=1.0,
    )


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _RecordingClient:
    """Stands in for the caller-owned httpx.AsyncClient compute_shadow_score now requires.

    Returns a fixed unit vector per call, tagged by which text it embedded.
    """

    def __init__(self, *, model_name: str = "intfloat/multilingual-e5-small") -> None:
        self.calls: list[dict] = []
        self._model_name = model_name

    async def post(self, url: str, *, json: dict, timeout: float) -> _FakeResponse:
        self.calls.append(json)
        text = json["texts"][0]
        # deterministic unit vector so cosine similarity is well-defined and non-trivial
        vector = [1.0, 0.0] if text.startswith("passage: ") else [0.8, 0.6]
        return _FakeResponse(
            {"vectors": [vector], "model_name": self._model_name, "model_revision": "main"}
        )


class _FailingClient:
    async def post(self, *args: object, **kwargs: object) -> _FakeResponse:
        raise httpx.ConnectTimeout("embedding service unreachable")


def test_cold_cache_calls_the_embedding_service_and_stores_both_vectors() -> None:
    async def run() -> None:
        client = _RecordingClient()
        session_factory = _session_factory()
        with session_factory() as session:
            result = await compute_shadow_score(
                session,
                _settings(),
                client,
                vacancy_id="vac-1",
                vacancy_text="Python backend engineer",
                resume_id="cv-1",
                resume_text="Python developer, 4 years",
            )
            assert result.used_fallback is False
            assert result.vacancy_cache_hit is False
            assert result.resume_cache_hit is False
            assert result.e5_score == pytest.approx(0.8, abs=1e-6)
            assert len(client.calls) == 2
            assert client.calls[0]["texts"] == ["passage: Python backend engineer"]
            assert client.calls[1]["texts"] == ["query: Python developer, 4 years"]
            stored = session.scalars(select(MatchingEmbeddingCacheRow)).all()
            assert {row.entity_type for row in stored} == {"vacancy", "resume"}

    asyncio.run(run())


def test_warm_cache_skips_the_embedding_service() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            first_client = _RecordingClient()
            await compute_shadow_score(
                session,
                _settings(),
                first_client,
                vacancy_id="vac-1",
                vacancy_text="Python backend engineer",
                resume_id="cv-1",
                resume_text="Python developer, 4 years",
            )
            assert len(first_client.calls) == 2

            second_client = _RecordingClient()
            result = await compute_shadow_score(
                session,
                _settings(),
                second_client,
                vacancy_id="vac-1",
                vacancy_text="Python backend engineer",
                resume_id="cv-1",
                resume_text="Python developer, 4 years",
            )
            assert result.vacancy_cache_hit is True
            assert result.resume_cache_hit is True
            assert second_client.calls == []

    asyncio.run(run())


def test_embedding_service_failure_falls_back_without_raising() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            result = await compute_shadow_score(
                session,
                _settings(),
                _FailingClient(),
                vacancy_id="vac-1",
                vacancy_text="Python backend engineer",
                resume_id="cv-1",
                resume_text="Python developer, 4 years",
            )
        assert result.used_fallback is True
        assert result.e5_score is None
        assert result.latency_ms >= 0

    asyncio.run(run())


def test_model_mismatch_is_not_cached() -> None:
    async def run() -> None:
        client = _RecordingClient(model_name="some-other-model")
        session_factory = _session_factory()
        with session_factory() as session:
            result = await compute_shadow_score(
                session,
                _settings(),
                client,
                vacancy_id="vac-1",
                vacancy_text="Python backend engineer",
                resume_id="cv-1",
                resume_text="Python developer, 4 years",
            )
            assert result.used_fallback is False
            assert session.scalars(select(MatchingEmbeddingCacheRow)).all() == []

    asyncio.run(run())
