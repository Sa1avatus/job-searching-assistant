from __future__ import annotations

from sqlalchemy import Text, create_engine, inspect
from sqlalchemy.orm import Session

from app.storage.tables import Base, LlmPreferenceRow, UserRow


def test_llm_preference_base_url_is_optional_text() -> None:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)

    column = next(
        value
        for value in inspect(engine).get_columns("llm_preferences")
        if value["name"] == "base_url"
    )
    assert column["nullable"] is True
    assert isinstance(column["type"], Text)

    with Session(engine) as session:
        session.add_all(
            [
                UserRow(id="user-1", display_name="First"),
                UserRow(id="user-2", display_name="Second"),
            ]
        )
        session.add_all(
            [
                LlmPreferenceRow(
                    user_id="user-1",
                    provider="gemini",
                    model="gemini-model",
                    base_url=None,
                    encrypted_api_key="ciphertext-1",
                ),
                LlmPreferenceRow(
                    user_id="user-2",
                    provider="openai_compatible",
                    model="custom-model",
                    base_url="https://models.example.test/v1",
                    encrypted_api_key="ciphertext-2",
                ),
            ]
        )
        session.commit()

        assert session.get(LlmPreferenceRow, "user-1").base_url is None
        assert session.get(LlmPreferenceRow, "user-2").base_url == (
            "https://models.example.test/v1"
        )
