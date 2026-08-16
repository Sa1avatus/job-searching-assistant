from __future__ import annotations

from dataclasses import dataclass

import pytest
from pydantic import SecretStr
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.matching.rag_client import RagDocumentResult
from app.services.rag_sync import RagSyncService
from app.storage.database import Base
from app.storage.tables import ApplicationRow, CvFileRow, ProfileFactRow, UserRow, VacancyRow


@dataclass
class FakeRagClient:
    ingested: list[dict[str, object]]
    deleted: list[dict[str, object]]

    async def ingest_document(self, **kwargs) -> RagDocumentResult:
        self.ingested.append(kwargs)
        return RagDocumentResult(
            document_id="rag-doc-1",
            external_document_id=str(kwargs["external_document_id"]),
            version=1,
            status="indexed",
            content_hash="abc123",
        )

    async def delete_document(self, **kwargs) -> bool:
        self.deleted.append(kwargs)
        return True


@pytest.mark.asyncio
async def test_sync_routes_profile_and_resume_to_separate_collections(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user = UserRow(display_name="Alice")
            session.add(user)
            session.flush()
            session.add(
                ProfileFactRow(
                    user_id=user.id,
                    category="skill",
                    name="Python",
                    value="production",
                    is_verified=True,
                )
            )
            cv = CvFileRow(
                user_id=user.id,
                original_filename="resume.pdf",
                storage_path="resume.pdf",
                content_type="application/pdf",
                sha256="a" * 64,
                size_bytes=1000,
                skills=["Python"],
                experience_summary="Built APIs.",
            )
            session.add(cv)
            session.commit()

            rag = FakeRagClient(ingested=[], deleted=[])
            monkeypatch.setattr("app.services.rag_sync.create_rag_client", lambda **_kwargs: rag)
            settings = Settings(
                rag_enabled=True,
                rag_service_url="http://rag.test",
                rag_api_key=SecretStr("test-key"),
                rag_project_id="project-1",
            )

            service = RagSyncService(session, settings)
            await service.sync_profile(user.id)
            await service.sync_resume(cv.id)

            assert [item["collection"] for item in rag.ingested] == ["profiles", "resumes"]
            assert all(item["owner_user_id"] == user.id for item in rag.ingested)

            deleted = await service.delete_resume(user.id, cv.id)
            assert deleted is True
            assert rag.deleted == [
                {
                    "owner_user_id": user.id,
                    "external_document_id": f"cv:{cv.id}",
                    "collection": "resumes",
                }
            ]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_delete_owner_documents_cleans_all_owner_scoped_collections(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            rag = FakeRagClient(ingested=[], deleted=[])
            monkeypatch.setattr("app.services.rag_sync.create_rag_client", lambda **_kwargs: rag)
            service = RagSyncService(session, Settings(_env_file=None, rag_enabled=False))

            result = await service.delete_owner_documents(
                "owner-1",
                cv_file_ids=("cv-1", "cv-2"),
                vacancy_ids=("vacancy-1",),
            )

            assert result.attempted == result.deleted == 4
            assert result.failed == 0
            assert rag.deleted == [
                {
                    "owner_user_id": "owner-1",
                    "external_document_id": "profile:owner-1",
                    "collection": "profiles",
                },
                {
                    "owner_user_id": "owner-1",
                    "external_document_id": "cv:cv-1",
                    "collection": "resumes",
                },
                {
                    "owner_user_id": "owner-1",
                    "external_document_id": "cv:cv-2",
                    "collection": "resumes",
                },
                {
                    "owner_user_id": "owner-1",
                    "external_document_id": "vacancy:vacancy-1",
                    "collection": "vacancies",
                },
            ]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_delete_owner_documents_continues_after_one_failure(monkeypatch) -> None:
    class PartiallyFailingRagClient(FakeRagClient):
        async def delete_document(self, **kwargs) -> bool:
            self.deleted.append(kwargs)
            if kwargs["external_document_id"] == "cv:cv-1":
                raise ConnectionError("unavailable")
            return False

    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            rag = PartiallyFailingRagClient(ingested=[], deleted=[])
            monkeypatch.setattr("app.services.rag_sync.create_rag_client", lambda **_kwargs: rag)
            service = RagSyncService(session, Settings(_env_file=None, rag_enabled=False))

            result = await service.delete_owner_documents(
                "owner-1",
                cv_file_ids=("cv-1",),
                vacancy_ids=("vacancy-1",),
            )

            assert result.attempted == 3
            assert result.deleted == 0
            assert result.failed == 1
            assert len(rag.deleted) == 3
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_backfill_owner_is_bounded_and_resumable(monkeypatch) -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user = UserRow(display_name="Alice")
            session.add(user)
            session.flush()
            session.add(
                ProfileFactRow(
                    user_id=user.id,
                    category="skill",
                    name="Python",
                    value="production",
                    is_verified=True,
                )
            )
            cvs = [
                CvFileRow(
                    id=f"cv-{index}",
                    user_id=user.id,
                    original_filename=f"resume-{index}.pdf",
                    storage_path=f"resume-{index}.pdf",
                    content_type="application/pdf",
                    sha256=str(index) * 64,
                    size_bytes=1000,
                    skills=["Python"],
                )
                for index in (1, 2)
            ]
            vacancies = [
                VacancyRow(
                    id=f"vacancy-{index}",
                    source_url=f"https://example.test/{index}",
                    title=f"Engineer {index}",
                    company="Example",
                )
                for index in (1, 2, 3)
            ]
            session.add_all([*cvs, *vacancies])
            session.flush()
            session.add_all(
                ApplicationRow(
                    user_id=user.id,
                    vacancy_id=vacancy.id,
                    selected_cv_file_id=cvs[0].id,
                    status="awaiting_review",
                    match_score=50,
                )
                for vacancy in vacancies
            )
            session.commit()

            rag = FakeRagClient(ingested=[], deleted=[])
            monkeypatch.setattr("app.services.rag_sync.create_rag_client", lambda **_kwargs: rag)
            service = RagSyncService(session, Settings(_env_file=None, rag_enabled=False))

            first = await service.backfill_owner(user.id, batch_size=1)
            second = await service.backfill_owner(
                user.id,
                batch_size=1,
                after_resume_id=first.next_resume_cursor,
                after_vacancy_id=first.next_vacancy_cursor,
            )
            third = await service.backfill_owner(
                user.id,
                batch_size=1,
                after_resume_id=second.next_resume_cursor,
                after_vacancy_id=second.next_vacancy_cursor,
            )

            assert first.resumes_attempted == first.resumes_indexed == 1
            assert first.resumes_skipped == 0
            assert first.resumes_failed == 0
            assert first.vacancies_attempted == first.vacancies_indexed == 1
            assert first.vacancies_skipped == 0
            assert first.vacancies_failed == 0
            assert first.next_resume_cursor == "cv-1"
            assert first.next_vacancy_cursor == "vacancy-1"
            assert first.resumes_has_more is True
            assert first.vacancies_has_more is True
            assert second.next_resume_cursor == "cv-2"
            assert second.next_vacancy_cursor == "vacancy-2"
            assert second.resumes_has_more is False
            assert second.vacancies_has_more is True
            assert third.resumes_attempted == 0
            assert third.vacancies_attempted == third.vacancies_indexed == 1
            assert third.next_resume_cursor == "cv-2"
            assert third.next_vacancy_cursor == "vacancy-3"
            assert third.resumes_has_more is False
            assert third.vacancies_has_more is False
            assert [item["collection"] for item in rag.ingested] == [
                "profiles",
                "resumes",
                "vacancies",
                "profiles",
                "resumes",
                "vacancies",
                "profiles",
                "vacancies",
            ]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_backfill_reports_disabled_rag_as_skipped_not_failed() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            user = UserRow(display_name="Offline")
            session.add(user)
            session.flush()
            cv = CvFileRow(
                user_id=user.id,
                original_filename="resume.pdf",
                storage_path="resume.pdf",
                content_type="application/pdf",
                sha256="f" * 64,
                size_bytes=1000,
                skills=["Python"],
            )
            vacancy = VacancyRow(
                source_url="https://example.test/offline",
                title="Engineer",
                company="Example",
            )
            session.add_all((cv, vacancy))
            session.flush()
            session.add(
                ApplicationRow(
                    user_id=user.id,
                    vacancy_id=vacancy.id,
                    selected_cv_file_id=cv.id,
                    status="draft",
                    match_score=50,
                )
            )
            session.commit()

            result = await RagSyncService(
                session,
                Settings(_env_file=None, rag_enabled=False),
            ).backfill_owner(user.id)

            assert result.profile_indexed is False
            assert result.resumes_indexed == 0
            assert result.resumes_skipped == 1
            assert result.resumes_failed == 0
            assert result.vacancies_indexed == 0
            assert result.vacancies_skipped == 1
            assert result.vacancies_failed == 0
    finally:
        engine.dispose()
