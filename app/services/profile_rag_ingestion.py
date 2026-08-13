from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.matching.rag_client import RagClient, RagDocumentResult
from app.matching.rag_collections import PROFILE_COLLECTION, RESUME_COLLECTION
from app.storage.tables import CvFileRow, ProfileFactRow, UserRow

logger = structlog.get_logger(__name__)


class ProfileRagIngestionService:
    def __init__(self, session: Session, rag_client: RagClient) -> None:
        self._session = session
        self._rag = rag_client

    async def ingest_profile(
        self,
        user_id: str,
        *,
        collection: str = PROFILE_COLLECTION,
    ) -> RagDocumentResult | None:
        user = self._session.get(UserRow, user_id)
        if user is None:
            logger.warning("rag_ingest_profile_not_found", user_id=user_id)
            return None
        content = self._build_content(user_id)
        metadata = {
            "source_type": "profile",
            "source_id": user_id,
            "display_name": user.display_name,
        }
        try:
            result = await self._rag.ingest_document(
                owner_user_id=user_id,
                external_document_id=f"profile:{user_id}",
                content=content,
                collection=collection,
                title=f"Profile: {user.display_name}",
                document_type="text",
                metadata=metadata,
            )
            logger.info(
                "rag_profile_ingested",
                user_id=user_id,
                document_id=result.document_id,
                status=result.status,
            )
            return result
        except Exception as error:
            logger.warning(
                "rag_profile_ingest_failed",
                user_id=user_id,
                error_type=type(error).__name__,
                error=str(error)[:200],
            )
            return None

    async def ingest_cv(
        self,
        cv_file_id: str,
        *,
        collection: str = RESUME_COLLECTION,
    ) -> RagDocumentResult | None:
        cv = self._session.get(CvFileRow, cv_file_id)
        if cv is None:
            logger.warning("rag_ingest_cv_not_found", cv_file_id=cv_file_id)
            return None
        content_parts: list[str] = []
        if cv.experience_summary:
            content_parts.append(cv.experience_summary)
        if cv.skills:
            content_parts.append(f"Skills: {', '.join(cv.skills)}")
        if cv.search_keywords:
            content_parts.append(f"Keywords: {cv.search_keywords}")
        content = "\n".join(content_parts)
        if not content.strip():
            logger.warning("rag_ingest_cv_empty", cv_file_id=cv_file_id)
            return None
        metadata = {
            "source_type": "cv",
            "source_id": cv.id,
            "user_id": cv.user_id,
            "filename": cv.original_filename,
            "years_of_experience": cv.years_of_experience,
        }
        try:
            result = await self._rag.ingest_document(
                owner_user_id=cv.user_id,
                external_document_id=f"cv:{cv.id}",
                content=content,
                collection=collection,
                title=f"CV: {cv.original_filename}",
                document_type="text",
                metadata=metadata,
            )
            logger.info(
                "rag_cv_ingested",
                cv_id=cv.id,
                document_id=result.document_id,
                status=result.status,
            )
            return result
        except Exception as error:
            logger.warning(
                "rag_cv_ingest_failed",
                cv_id=cv.id,
                error_type=type(error).__name__,
                error=str(error)[:200],
            )
            return None

    def _build_content(self, user_id: str) -> str:
        facts = list(
            self._session.scalars(
                select(ProfileFactRow).where(
                    ProfileFactRow.user_id == user_id,
                    ProfileFactRow.is_verified.is_(True),
                )
            ).all()
        )
        parts: list[str] = []
        for fact in facts:
            parts.append(f"{fact.category}/{fact.name}: {fact.value}")
        return "\n".join(parts)
