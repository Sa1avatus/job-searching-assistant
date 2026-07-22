from io import BytesIO
from pathlib import Path
from zipfile import ZipFile

import pytest

from app.storage.documents import DocumentStorage, InvalidDocumentError


def _docx_bytes() -> bytes:
    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types />")
        archive.writestr("word/document.xml", "<document />")
    return stream.getvalue()


def test_document_storage_uses_generated_path_and_hash(tmp_path: Path) -> None:
    storage = DocumentStorage(tmp_path / "documents", max_document_bytes=1024)

    saved = storage.save("../candidate.pdf", "application/pdf", b"%PDF-1.7\nfixture")

    assert saved.storage_path.parent == (tmp_path / "documents").resolve()
    assert saved.storage_path.name != "candidate.pdf"
    assert saved.original_filename == "candidate.pdf"
    assert len(saved.sha256) == 64


def test_document_storage_validates_docx_structure(tmp_path: Path) -> None:
    storage = DocumentStorage(tmp_path, max_document_bytes=2048)

    saved = storage.save(
        "resume.docx",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        _docx_bytes(),
    )

    assert saved.storage_path.exists()


def test_document_storage_rejects_spoofed_and_oversized_files(tmp_path: Path) -> None:
    storage = DocumentStorage(tmp_path, max_document_bytes=16)

    with pytest.raises(InvalidDocumentError):
        storage.save("resume.pdf", "application/pdf", b"not a pdf")
    with pytest.raises(InvalidDocumentError):
        storage.save("resume.pdf", "application/pdf", b"%PDF-" + b"x" * 20)


def test_document_storage_rebases_container_and_windows_paths(tmp_path: Path) -> None:
    storage = DocumentStorage(tmp_path / "documents", max_document_bytes=1024)
    saved = storage.save("resume.pdf", "application/pdf", b"%PDF-1.7\nfixture")

    assert (
        storage.resolve(f"/app/.artifacts/documents/{saved.storage_path.name}")
        == saved.storage_path
    )
    assert (
        storage.resolve(f"C:\\project\\.artifacts\\documents\\{saved.storage_path.name}")
        == saved.storage_path
    )


@pytest.mark.parametrize(
    ("filename", "content_type", "content"),
    [
        ("resume.txt", "text/plain", b"plain resume text"),
        ("resume.rtf", "application/rtf", b"{\\rtf1 plain resume text}"),
        (
            "resume.doc",
            "application/msword",
            b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1legacy resume text",
        ),
    ],
)
def test_document_storage_accepts_additional_resume_formats(
    tmp_path: Path, filename: str, content_type: str, content: bytes
) -> None:
    storage = DocumentStorage(tmp_path, max_document_bytes=2048)

    assert storage.save(filename, content_type, content).storage_path.is_file()
