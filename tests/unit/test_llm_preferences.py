import httpx
import pytest
from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.llm.preferences import (
    LlmPreferencePurpose,
    LlmPreferenceService,
    fetch_available_models,
    resolve_preference,
)
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
        assert session.get(LlmPreferenceRow, (user.id, "materials")) is not None
        assert preference is not None
        assert preference.provider == "gemini"
        assert preference.model == "gemini-test"
        assert preference.api_key == "secret-test-key"
        assert preference.base_url is None


def test_preference_service_normalizes_openai_compatible_base_url() -> None:
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
            provider="openai_compatible",
            model="custom-model",
            api_key="secret-test-key",
            base_url="https://models.example.test/v1/",
        )
        preference = service.load(user.id)

        assert row.base_url == "https://models.example.test/v1"
        assert preference is not None
        assert preference.base_url == "https://models.example.test/v1"


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


@pytest.mark.asyncio
async def test_fetch_available_openai_compatible_models_uses_custom_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == httpx.URL("https://models.example.test/v1/models")
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(
            200,
            json={"data": [{"id": "model-b"}, {"id": "model-a"}, {"id": "model-a"}]},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        models = await fetch_available_models(
            client,
            provider="openai_compatible",
            api_key="test-key",
            base_url="https://models.example.test/v1/",
        )

    assert models == ["model-a", "model-b"]


def test_preference_service_saves_and_loads_independent_purposes() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.commit()
        service = LlmPreferenceService(
            session, encryption_key=Fernet.generate_key().decode("ascii")
        )

        service.save(
            user_id=user.id,
            provider="gemini",
            model="materials-model",
            api_key="key-1",
            purpose="materials",
        )
        service.save(
            user_id=user.id,
            provider="openai_compatible",
            model="matching-model",
            api_key="key-2",
            base_url="https://models.example.test/v1",
            purpose="matching",
        )

        materials = service.load(user.id, "materials")
        matching = service.load(user.id, "matching")

        assert materials is not None
        assert materials.model == "materials-model"
        assert materials.provider == "gemini"
        assert matching is not None
        assert matching.model == "matching-model"
        assert matching.provider == "openai_compatible"


def test_resolve_preference_matching_falls_back_to_materials() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        user = UserRow(display_name="Candidate")
        session.add(user)
        session.commit()
        key = Fernet.generate_key().decode("ascii")
        service = LlmPreferenceService(session, encryption_key=key)

        # Only the legacy single-model (materials) preference exists.
        service.save(
            user_id=user.id,
            provider="gemini",
            model="legacy-model",
            api_key="key-1",
            purpose="materials",
        )
        resolved = resolve_preference(session, user.id, key, LlmPreferencePurpose.MATCHING)
        assert resolved is not None
        assert resolved.model == "legacy-model"

        # An explicit matching row now wins over the fallback.
        service.save(
            user_id=user.id,
            provider="gemini",
            model="matching-model",
            api_key="key-2",
            purpose="matching",
        )
        resolved = resolve_preference(session, user.id, key, LlmPreferencePurpose.MATCHING)
        assert resolved is not None
        assert resolved.model == "matching-model"
