import pytest
from cryptography.fernet import Fernet
from httpx import AsyncClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.api.main import build_user_model_providers
from app.config import Settings
from app.llm.preferences import LlmPreferenceService
from app.llm.providers.openai_compatible import OpenAICompatibleProvider
from app.storage.database import Base
from app.storage.tables import UserRow


async def _build_openai_compatible_provider(*, reasoning_mitigation_enabled: bool):
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
                model="gemma4-12b",
                api_key="local-key",
                base_url="http://host.docker.internal:8033/v1",
            )

            async with AsyncClient() as client:
                providers = build_user_model_providers(
                    client,
                    session,
                    user.id,
                    Settings(
                        _env_file=None,
                        browser_state_encryption_key=encryption_key,
                        matching_llm_reasoning_mitigation_enabled=reasoning_mitigation_enabled,
                    ),
                )
            assert len(providers) == 1
            assert isinstance(providers[0], OpenAICompatibleProvider)
            return providers[0]
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_api_build_user_model_providers_defaults_mitigation_off() -> None:
    provider = await _build_openai_compatible_provider(reasoning_mitigation_enabled=False)
    assert provider._reasoning_mitigation_enabled is False


@pytest.mark.asyncio
async def test_api_build_user_model_providers_threads_reasoning_mitigation_flag() -> None:
    """extract_resume_profile (materials purpose) and other app.api.main LLM callers go
    through this same builder - it must thread the flag the same way
    app.matching.runtime's builder does, or chat_template_kwargs.enable_thinking never
    reaches a local reasoning-capable model outside the matching pipeline."""
    provider = await _build_openai_compatible_provider(reasoning_mitigation_enabled=True)
    assert provider._reasoning_mitigation_enabled is True
