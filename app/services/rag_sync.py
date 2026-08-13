from __future__ import annotations

from sqlalchemy.orm import Session

from app.config import Settings
from app.matching.rag_client import RagDocumentResult, create_rag_client
from app.matching.rag_collections import PROFILE_COLLECTION
from app.services.profile_rag_ingestion import ProfileRagIngestionService


class RagSyncService:
    def __init__(self, session: Session, settings: Settings) -> None:
        self._ingestion = ProfileRagIngestionService(
            session,
            create_rag_client(
                service_url=settings.rag_service_url,
                api_key=(
                    settings.rag_api_key.get_secret_value()
                    if settings.rag_api_key is not None
                    else None
                ),
                project_id=settings.rag_project_id,
                collection=PROFILE_COLLECTION,
                timeout_seconds=settings.rag_timeout_seconds,
                enabled=settings.rag_enabled,
            ),
        )

    async def sync_profile(self, user_id: str) -> RagDocumentResult | None:
        return await self._ingestion.ingest_profile(user_id)

    async def sync_resume(self, cv_file_id: str) -> RagDocumentResult | None:
        return await self._ingestion.ingest_cv(cv_file_id)
