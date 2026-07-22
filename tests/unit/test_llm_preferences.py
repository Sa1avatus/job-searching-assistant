import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.llm.preferences import LlmPreferenceService, fetch_available_models
from app.storage.database import Base
from app.storage.tables import LlmPreferenceRow, UserRow


def test_preference_service_encrypts_key_and_restores_selection() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.commit()
        service = LlmPreferenceService(
            session, encryption_key=Fernet.generate_key().decode("ascii")
        )

        row = service.save(
            user_id=user.id,
            provider="gemini",
            model="gemini-test",
            api_key="secret-test-key",
        )
        preference = service.load(user.id)

        assert "secret-test-key" not in row.encrypted_api_key
        assert session.get(LlmPreferenceRow, user.id) is not None
        assert preference is not None
        assert preference.provider == "gemini"
        assert preference.model == "gemini-test"
        assert preference.api_key == "secret-test-key"


@pytest.mark.asyncio
async def test_fetch_available_gemini_models_filters_non_generation_models() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["x-goog-api-key"] == "test-key"
        return httpx.Response(
            200,
            json={
                "models": [
                    {
                        "name": "models/gemini-generate",
                        "supportedGenerationMethods": ["generateContent"],
                    },
                    {
                        "name": "models/gemini-embed",
                        "supportedGenerationMethods": ["embedContent"],
                    },
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models = await fetch_available_models(client, provider="gemini", api_key="test-key")

    assert models == ["gemini-generate"]
