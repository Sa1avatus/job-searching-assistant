import pytest
from cryptography.fernet import Fernet

from app.security.autofill_decryption import decrypt_autofill_value
from app.security.autofill_encryption import InvalidAutofillValueEncryption


def _encrypt_test_value(plaintext: bytes) -> tuple[str, str]:
    encryption_key_bytes = Fernet.generate_key()
    token = Fernet(encryption_key_bytes).encrypt(plaintext).decode("ascii")
    return encryption_key_bytes.decode("ascii"), token


@pytest.mark.parametrize("plaintext", ["ordinary value", "\t  résumé 日本語  \n"])
def test_decrypt_autofill_value_returns_exact_text(plaintext: str) -> None:
    encryption_key, token = _encrypt_test_value(plaintext.encode("utf-8"))

    assert decrypt_autofill_value(token, encryption_key=encryption_key) == plaintext


def test_decrypt_autofill_value_rejects_non_text_token() -> None:
    encryption_key = Fernet.generate_key().decode("ascii")

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        decrypt_autofill_value(123, encryption_key=encryption_key)  # type: ignore[arg-type]

    assert str(error.value) == "Encrypted autofill value must be text"


def test_decrypt_autofill_value_rejects_empty_token() -> None:
    encryption_key = Fernet.generate_key().decode("ascii")

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        decrypt_autofill_value("", encryption_key=encryption_key)

    assert str(error.value) == "Encrypted autofill value cannot be empty"


@pytest.mark.parametrize("invalid_key", [123, "clé", "not-a-fernet-key"])
def test_decrypt_autofill_value_rejects_invalid_key(invalid_key: object) -> None:
    _, token = _encrypt_test_value(b"example")

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        decrypt_autofill_value(
            token,
            encryption_key=invalid_key,  # type: ignore[arg-type]
        )

    assert str(error.value) == "Autofill value encryption key is invalid"


def test_decrypt_autofill_value_rejects_malformed_token() -> None:
    encryption_key = Fernet.generate_key().decode("ascii")

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        decrypt_autofill_value("malformed", encryption_key=encryption_key)

    assert str(error.value) == "Encrypted autofill value is invalid"


def test_decrypt_autofill_value_rejects_non_ascii_token() -> None:
    encryption_key = Fernet.generate_key().decode("ascii")

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        decrypt_autofill_value("токен", encryption_key=encryption_key)

    assert str(error.value) == "Encrypted autofill value is invalid"


def test_decrypt_autofill_value_rejects_token_signed_by_another_key() -> None:
    _, token = _encrypt_test_value(b"private test value")
    wrong_key = Fernet.generate_key().decode("ascii")

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        decrypt_autofill_value(token, encryption_key=wrong_key)

    error_message = str(error.value)
    assert error_message == "Encrypted autofill value is invalid"
    assert token not in error_message
    assert wrong_key not in error_message
    assert "private test value" not in error_message


@pytest.mark.parametrize("plaintext_bytes", [b"", b"\xff\xfe"])
def test_decrypt_autofill_value_rejects_invalid_plaintext(
    plaintext_bytes: bytes,
) -> None:
    encryption_key, token = _encrypt_test_value(plaintext_bytes)

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        decrypt_autofill_value(token, encryption_key=encryption_key)

    assert str(error.value) == "Encrypted autofill value is invalid"
