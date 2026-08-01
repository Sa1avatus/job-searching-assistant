from cryptography.fernet import Fernet


class InvalidAutofillValueEncryption(ValueError):
    pass


def encrypt_autofill_value(serialized_value: str, *, encryption_key: str) -> str:
    if not isinstance(serialized_value, str):
        raise InvalidAutofillValueEncryption("Autofill value must be text")
    if not serialized_value:
        raise InvalidAutofillValueEncryption("Autofill value cannot be empty")
    if not isinstance(encryption_key, str):
        raise InvalidAutofillValueEncryption("Autofill value encryption key is invalid")
    try:
        cipher = Fernet(encryption_key.encode("ascii"))
    except (UnicodeEncodeError, ValueError) as error:
        raise InvalidAutofillValueEncryption("Autofill value encryption key is invalid") from error
    return cipher.encrypt(serialized_value.encode("utf-8")).decode("ascii")
