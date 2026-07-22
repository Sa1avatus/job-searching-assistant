"""Extract plain text from a resume file (PDF or DOCX) for LLM-based fact extraction.

Deliberately does not try to preserve layout/formatting — only enough text to let a model read
the candidate's skills and work history. Scanned/image-only PDFs will yield little or no text;
callers should treat an empty/very short result as "could not read this resume" rather than
silently proceeding.
"""

from __future__ import annotations

import io

import docx
from pypdf import PdfReader

MIN_USABLE_TEXT_LENGTH = 40


class UnreadableResumeError(RuntimeError):
    """The file could not be parsed, or contained too little extractable text."""


def extract_resume_text(content: bytes, *, extension: str) -> str:
    extension = extension.casefold()
    if extension == ".pdf":
        text = _extract_pdf_text(content)
    elif extension == ".docx":
        text = _extract_docx_text(content)
    else:
        raise UnreadableResumeError(f"Unsupported resume extension: {extension}")
    text = text.strip()
    if len(text) < MIN_USABLE_TEXT_LENGTH:
        raise UnreadableResumeError(
            "Could not extract usable text from this resume (it may be a scanned image)"
        )
    return text


def _extract_pdf_text(content: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(content))
        pages = [page.extract_text() or "" for page in reader.pages]
    except Exception as error:  # noqa: BLE001 - pypdf raises assorted internal exceptions
        raise UnreadableResumeError(f"Could not parse PDF: {error}") from error
    return "\n".join(pages)


def _extract_docx_text(content: bytes) -> str:
    try:
        document = docx.Document(io.BytesIO(content))
    except Exception as error:  # noqa: BLE001 - python-docx raises assorted internal exceptions
        raise UnreadableResumeError(f"Could not parse DOCX: {error}") from error
    paragraphs = [paragraph.text for paragraph in document.paragraphs]
    for table in document.tables:
        for row in table.rows:
            paragraphs.extend(cell.text for cell in row.cells)
    return "\n".join(paragraphs)
