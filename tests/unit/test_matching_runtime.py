import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import Settings
from app.llm.preferences import LlmPreferenceService
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.matching.runtime import _build_user_model_providers
from app.storage.database import Base
from app.storage.tables import UserRow


@pytest.mark.asyncio
async def test_matching_runtime_uses_openai_compatible_user_preference() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    encryption_key = Fernet.generate_key().decode("ascii")
    try:
        with Session(engine) as session:
            user = UserRow(display_name="Candidate")
            session.add(user)
            session.commit()
            LlmPreferenceService(session, encryption_key=encryption_key).save(
                user_id=user.id,
                provider="openai_compatible",
                model="local-model",
                api_key="local-key",
                base_url="http://host.docker.internal:1234/v1",
            )

            async with AsyncClient() as client:
                providers = _build_user_model_providers(
                    client,
                    session,
                    user.id,
                    Settings(_env_file=None, browser_state_encryption_key=encryption_key),
                )

            assert len(providers) == 1
            assert isinstance(providers[0], OpenAICompatibleProvider)
    finally:
        engine.dispose()
