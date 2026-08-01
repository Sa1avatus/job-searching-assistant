import pytest
from cryptography.fernet import Fernet

from app.security.autofill_encryption import (
    InvalidAutofillValueEncryption,
    encrypt_autofill_value,
)


def test_encrypt_autofill_value_returns_random_ascii_ciphertext() -> None:
    encryption_key = Fernet.generate_key().decode("ascii")

    first = encrypt_autofill_value("example value", encryption_key=encryption_key)
    second = encrypt_autofill_value("example value", encryption_key=encryption_key)

    assert first.isascii()
    assert first != "example value"
    assert first != second


@pytest.mark.parametrize("serialized_value", ["ordinary value", "\t  résumé 日本語  \n"])
def test_encrypt_autofill_value_round_trips_exact_text(serialized_value: str) -> None:
    encryption_key_bytes = Fernet.generate_key()
    encryption_key = encryption_key_bytes.decode("ascii")

    ciphertext = encrypt_autofill_value(
        serialized_value,
        encryption_key=encryption_key,
    )
    decrypted_value = (
        Fernet(encryption_key_bytes).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    )

    assert decrypted_value == serialized_value


def test_encrypt_autofill_value_rejects_empty_value_with_exact_error() -> None:
    encryption_key = Fernet.generate_key().decode("ascii")

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        encrypt_autofill_value("", encryption_key=encryption_key)

    assert str(error.value) == "Autofill value cannot be empty"


def test_encrypt_autofill_value_rejects_non_text_value_with_exact_error() -> None:
    encryption_key = Fernet.generate_key().decode("ascii")

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        encrypt_autofill_value(123, encryption_key=encryption_key)  # type: ignore[arg-type]

    assert str(error.value) == "Autofill value must be text"


@pytest.mark.parametrize(
    "invalid_key",
    [
        "not-a-fernet-key",
        "clé-française",
        123,
    ],
)
def test_encrypt_autofill_value_rejects_invalid_key_without_disclosure(
    invalid_key: object,
) -> None:
    serialized_value = "private test value"

    with pytest.raises(InvalidAutofillValueEncryption) as error:
        encrypt_autofill_value(
            serialized_value,
            encryption_key=invalid_key,  # type: ignore[arg-type]
        )

    error_message = str(error.value)
    assert error_message == "Autofill value encryption key is invalid"
    assert serialized_value not in error_message
    assert str(invalid_key) not in error_message
