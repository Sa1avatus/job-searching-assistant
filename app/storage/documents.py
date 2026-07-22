from __future__ import annotations

import hashlib
import os
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path, PurePosixPath


class InvalidDocumentError(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class SavedDocument:
    original_filename: str
    storage_path: Path
    content_type: str
    sha256: str
    size_bytes: int


ALLOWED_DOCUMENTS: dict[str, tuple[str, ...]] = {
    ".docx": ("application/vnd.openxmlformats-officedocument.wordprocessingml.document",),
    ".pdf": ("application/pdf",),
}


class DocumentStorage:
    def __init__(self, root_directory: Path, *, max_document_bytes: int) -> None:
        self._root_directory = root_directory.resolve()
        self._max_document_bytes = max_document_bytes

    def save(self, original_filename: str, content_type: str, content: bytes) -> SavedDocument:
        normalized_filename = Path(original_filename).name
        if (
            not normalized_filename
            or len(normalized_filename) > 255
            or any(ord(character) < 32 for character in normalized_filename)
        ):
            raise InvalidDocumentError("Document filename is invalid")
        extension = Path(normalized_filename).suffix.casefold()
        if extension not in ALLOWED_DOCUMENTS:
            raise InvalidDocumentError("Only PDF and DOCX documents are supported")
        if content_type not in ALLOWED_DOCUMENTS[extension]:
            raise InvalidDocumentError("Document extension and content type do not match")
        if not content or len(content) > self._max_document_bytes:
            raise InvalidDocumentError("Document is empty or exceeds the configured size limit")
        if extension == ".pdf" and not content.startswith(b"%PDF-"):
            raise InvalidDocumentError("PDF signature is invalid")
        if extension == ".docx" and not self._is_docx(content):
            raise InvalidDocumentError("DOCX structure is invalid")

        self._root_directory.mkdir(parents=True, exist_ok=True)
        storage_path = self._root_directory / f"{uuid.uuid4()}{extension}"
        temporary_path = storage_path.with_suffix(f"{extension}.tmp")
        try:
            with temporary_path.open("xb") as stream:
                stream.write(content)
                stream.flush()
                os.fsync(stream.fileno())
            temporary_path.replace(storage_path)
        finally:
            temporary_path.unlink(missing_ok=True)
        return SavedDocument(
            original_filename=normalized_filename,
            storage_path=storage_path,
            content_type=content_type,
            sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
        )

    def resolve(self, storage_path: str | Path) -> Path:
        """Resolve a DB path written by either Windows or the Docker container."""
        normalized = str(storage_path).replace("\\", "/")
        filename = PurePosixPath(normalized).name
        if not filename or filename in {".", ".."}:
            raise InvalidDocumentError("Document path is invalid")
        resolved_path = (self._root_directory / filename).resolve()
        if not resolved_path.is_relative_to(self._root_directory):
            raise InvalidDocumentError("Document path is outside the storage root")
        return resolved_path

    def delete(self, storage_path: str | Path) -> None:
        self.resolve(storage_path).unlink(missing_ok=True)

    @staticmethod
    def _is_docx(content: bytes) -> bool:
        try:
            with zipfile.ZipFile(BytesIO(content)) as archive:
                names = frozenset(archive.namelist())
        except (zipfile.BadZipFile, OSError):
            return False
        return "[Content_Types].xml" in names and "word/document.xml" in names
