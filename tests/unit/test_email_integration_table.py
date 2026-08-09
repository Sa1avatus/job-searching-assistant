from sqlalchemy import Boolean, CheckConstraint, Integer, String, Text, create_engine
from sqlalchemy.orm import Session

from app.storage.database import Base
from app.storage.tables import EmailIntegrationRow, UserRow


def test_email_integration_row_stores_only_encrypted_password() -> None:
    assert EmailIntegrationRow.__tablename__ == "email_integrations"
    columns = set(EmailIntegrationRow.__table__.columns.keys())
    assert columns == {
        "user_id",
        "host",
        "port",
        "username",
        "encrypted_password",
        "use_ssl",
        "mailbox",
        "enabled",
        "created_at",
        "updated_at",
    }
    assert "password" not in columns

    assert isinstance(EmailIntegrationRow.__table__.c.host.type, String)
    assert isinstance(EmailIntegrationRow.__table__.c.port.type, Integer)
    assert isinstance(EmailIntegrationRow.__table__.c.encrypted_password.type, Text)
    assert isinstance(EmailIntegrationRow.__table__.c.use_ssl.type, Boolean)
    assert EmailIntegrationRow.__table__.c.user_id.primary_key is True

    constraints = [
        item
        for item in EmailIntegrationRow.__table__.constraints
        if isinstance(item, CheckConstraint)
    ]
    assert any(
        item.name == "ck_email_integrations_port_range" and "65535" in item.sqltext.text
        for item in constraints
    )


def test_email_integration_row_defaults_are_safe() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            row = EmailIntegrationRow(
                user_id="user-1",
                host="imap.example.test",
                username="candidate@example.test",
                encrypted_password="encrypted-secret",
            )
            session.add(row)
            session.commit()

            assert row.port == 993
            assert row.use_ssl is True
            assert row.mailbox == "INBOX"
            assert row.enabled is True
    finally:
        engine.dispose()
