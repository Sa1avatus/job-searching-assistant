import asyncio
from datetime import datetime

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.matching.indexing import EvidenceReindexService
from app.matching.opensearch_index import EvidenceDocument
from app.matching.semantic import FakeEmbeddingClient
from app.storage.database import Base
from app.storage.tables import (
    CandidateEvidenceRow,
    CvFileRow,
    EmbeddingRecordRow,
    UserRow,
)


class _FakeIndexWriter:
    def __init__(self, *, fail_indexing: bool = False) -> None:
        self.fail_indexing = fail_indexing
        self.created: list[str] = []
        self.indexed: list[EvidenceDocument] = []
        self.switched: list[str] = []
        self.deleted: list[str] = []

    def versioned_index_name(self, *, created_at: datetime | None = None) -> str:
        return "candidate-evidence-v1-test"

    async def create_index(self, index_name: str) -> None:
        self.created.append(index_name)

    async def switch_aliases(self, index_name: str) -> None:
        self.switched.append(index_name)

    async def index_documents(
        self,
        documents: tuple[EvidenceDocument, ...],
        *,
        target_index: str | None = None,
    ) -> None:
        assert target_index == "candidate-evidence-v1-test"
        if self.fail_indexing:
            raise ConnectionError("index failed")
        self.indexed.extend(documents)

    async def delete_index(self, index_name: str) -> None:
        self.deleted.append(index_name)


def _session_factory() -> sessionmaker:
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)


def _add_evidence(session: object, *, is_verified: bool = True) -> CandidateEvidenceRow:
    session.add(user := UserRow(display_name="Candidate"))  # type: ignore[attr-defined]
    session.flush()  # type: ignore[attr-defined]
    session.add(  # type: ignore[attr-defined]
        cv_file := CvFileRow(
            user_id=user.id,
            original_filename="resume.txt",
            storage_path="resume.txt",
            content_type="text/plain",
            sha256="a" * 64,
            size_bytes=10,
        )
    )
    session.flush()  # type: ignore[attr-defined]
    evidence = CandidateEvidenceRow(
        user_id=user.id,
        cv_file_id=cv_file.id,
        evidence_text="Built Python services",
        normalized_text="python services",
        evidence_type="work_experience",
        skill_name="Python",
        experience_level="production",
        years=3,
        is_verified=is_verified,
        source_fragment="Built Python services",
        source_section="Experience",
        extraction_model="fake",
        extraction_model_version="1",
        extraction_schema_version="1",
        extraction_run_id="run-1",
        confidence=0.9,
    )
    session.add(evidence)  # type: ignore[attr-defined]
    session.commit()  # type: ignore[attr-defined]
    return evidence


def test_reindex_builds_from_verified_postgres_evidence_and_records_metadata() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            evidence = _add_evidence(session)
            writer = _FakeIndexWriter()
            service = EvidenceReindexService(
                session,
                FakeEmbeddingClient(dimensions=4),
                writer,
                batch_size=1,
            )

            index_name, count = await service.rebuild()
            record = session.scalar(select(EmbeddingRecordRow))

            assert index_name == "candidate-evidence-v1-test"
            assert count == 1
            assert writer.created == [index_name]
            assert writer.switched == [index_name]
            assert writer.indexed[0].evidence_id == evidence.id
            assert record is not None
            assert record.index_name == index_name
            assert record.dimensions == 4

    asyncio.run(run())


def test_reindex_is_idempotent_for_unchanged_content() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            _add_evidence(session)
            writer = _FakeIndexWriter()
            service = EvidenceReindexService(
                session,
                FakeEmbeddingClient(dimensions=4),
                writer,
            )

            await service.rebuild()
            await service.rebuild()

            assert len(session.scalars(select(EmbeddingRecordRow)).all()) == 1

    asyncio.run(run())


def test_reindex_failure_rolls_back_metadata_and_deletes_partial_index() -> None:
    async def run() -> None:
        session_factory = _session_factory()
        with session_factory() as session:
            _add_evidence(session)
            writer = _FakeIndexWriter(fail_indexing=True)
            service = EvidenceReindexService(
                session,
                FakeEmbeddingClient(dimensions=4),
                writer,
            )

            with pytest.raises(ConnectionError, match="index failed"):
                await service.rebuild()

            assert session.scalar(select(EmbeddingRecordRow)) is None
            assert writer.deleted == ["candidate-evidence-v1-test"]

    asyncio.run(run())
