from pathlib import Path

import pytest

from app.services.email_file_import import EmlImportProvider

_EML_CONTENT = b"""From: sender@example.com
To: recipient@example.com
Subject: Test email
Content-Type: text/plain; charset=utf-8

This is a test email body.
"""


@pytest.mark.asyncio
async def test_eml_import_reads_subject_and_body(tmp_path: Path) -> None:
    eml_file = tmp_path / "test.eml"
    eml_file.write_bytes(_EML_CONTENT)

    provider = EmlImportProvider([eml_file])
    messages = await provider.fetch_messages()

    assert len(messages) == 1
    assert messages[0].subject == "Test email"
    assert "test email body" in messages[0].body


@pytest.mark.asyncio
async def test_eml_import_handles_multiple_files(tmp_path: Path) -> None:
    for i in range(3):
        eml_file = tmp_path / f"email_{i}.eml"
        eml_file.write_bytes(
            f"From: sender{i}@example.com\nSubject: Email {i}\n\nBody {i}\n".encode()
        )

    provider = EmlImportProvider(list(tmp_path.glob("*.eml")))
    messages = await provider.fetch_messages()

    assert len(messages) == 3
    subjects = {m.subject for m in messages}
    assert "Email 0" in subjects


@pytest.mark.asyncio
async def test_eml_import_skips_corrupted_files(tmp_path: Path) -> None:
    good_file = tmp_path / "good.eml"
    good_file.write_bytes(_EML_CONTENT)
    bad_file = tmp_path / "bad.eml"
    bad_file.write_bytes(b"not a valid email")

    provider = EmlImportProvider([good_file, bad_file])
    messages = await provider.fetch_messages()

    assert len(messages) >= 1
    assert messages[0].subject == "Test email"


def test_eml_import_rejects_empty_path_list() -> None:
    with pytest.raises(ValueError, match="At least one EML path"):
        EmlImportProvider([])


def test_eml_import_rejects_too_many_files(tmp_path: Path) -> None:
    paths = [tmp_path / f"f{i}.eml" for i in range(101)]
    with pytest.raises(ValueError, match="Too many EML files"):
        EmlImportProvider(paths, max_files=100)
