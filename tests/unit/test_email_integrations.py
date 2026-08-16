from cryptography.fernet import Fernet
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.services.email_integrations import EmailIntegrationService, InvalidEmailIntegration
from app.storage.database import Base
from app.storage.tables import EmailIntegrationRow, UserRow


def test_email_integration_service_encrypts_and_loads_password() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.commit()
            service = EmailIntegrationService(
                session,
                encryption_key=Fernet.generate_key().decode("ascii"),
            )

            row = service.save(
                user_id="user-1",
                host=" IMAP.Example.Test ",
                port=993,
                username=" candidate@example.test ",
                password="app-password",
                use_ssl=True,
                mailbox=" INBOX ",
                enabled=True,
            )
            integration = service.load("user-1")

            assert "app-password" not in row.encrypted_password
            assert integration is not None
            assert integration.host == "imap.example.test"
            assert integration.username == "candidate@example.test"
            assert integration.password == "app-password"
            assert integration.mailbox == "INBOX"
    finally:
        engine.dispose()


def test_email_integration_update_preserves_password_when_omitted() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.commit()
            service = EmailIntegrationService(
                session,
                encryption_key=Fernet.generate_key().decode("ascii"),
            )
            original = service.save(
                user_id="user-1",
                host="imap.example.test",
                port=993,
                username="candidate@example.test",
                password="app-password",
                use_ssl=True,
                mailbox="INBOX",
                enabled=True,
            )
            encrypted_password = original.encrypted_password

            updated = service.save(
                user_id="user-1",
                host="imap.example.test",
                port=993,
                username="candidate@example.test",
                password=None,
                use_ssl=True,
                mailbox="Applications",
                enabled=False,
            )

            assert updated.encrypted_password == encrypted_password
            assert updated.mailbox == "Applications"
            assert updated.enabled is False
    finally:
        engine.dispose()


def test_email_integration_service_rejects_plaintext_password_omission_on_create() -> None:
    engine = create_engine("sqlite:///:memory:")
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as session:
            session.add(UserRow(id="user-1", display_name="Candidate"))
            session.commit()
            service = EmailIntegrationService(
                session,
                encryption_key=Fernet.generate_key().decode("ascii"),
            )

            try:
                service.save(
                    user_id="user-1",
                    host="imap.example.test",
                    port=993,
                    username="candidate@example.test",
                    password=None,
                    use_ssl=True,
                    mailbox="INBOX",
                    enabled=True,
                )
            except InvalidEmailIntegration as error:
                assert str(error) == "IMAP password is required"
            else:
                raise AssertionError("Expected InvalidEmailIntegration")

            assert session.get(EmailIntegrationRow, "user-1") is None
    finally:
        engine.dispose()
