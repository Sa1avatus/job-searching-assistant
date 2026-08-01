from cryptography.fernet import Fernet, InvalidToken

from app.security.autofill_encryption import InvalidAutofillValueEncryption


def decrypt_autofill_value(encrypted_value: str, *, encryption_key: str) -> str:
    if not isinstance(encrypted_value, str):
        raise InvalidAutofillValueEncryption("Encrypted autofill value must be text")
    if not encrypted_value:
        raise InvalidAutofillValueEncryption("Encrypted autofill value cannot be empty")
    if not isinstance(encryption_key, str):
        raise InvalidAutofillValueEncryption("Autofill value encryption key is invalid")
    try:
        cipher = Fernet(encryption_key.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as error:
        raise InvalidAutofillValueEncryption("Autofill value encryption key is invalid") from error
    try:
        plaintext_bytes = cipher.decrypt(encrypted_value.encode("ascii"))
    except (InvalidToken, UnicodeEncodeError) as error:
        raise InvalidAutofillValueEncryption("Encrypted autofill value is invalid") from error
    try:
        plaintext = plaintext_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise InvalidAutofillValueEncryption("Encrypted autofill value is invalid") from error
    if not plaintext:
        raise InvalidAutofillValueEncryption("Encrypted autofill value is invalid")
    return plaintext
