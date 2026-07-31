"""Extract plain text from a resume file (PDF or DOCX) for LLM-based fact extraction.

Deliberately does not try to preserve layout/formatting — only enough text to let a model read
the candidate's skills and work history. Scanned/image-only PDFs will yield little or no text;
callers should treat an empty/very short result as "could not read this resume" rather than
silently proceeding.
"""

from __future__ import annotations

import io
import re
import zipfile
from html.parser import HTMLParser
from xml.etree import ElementTree

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
    elif extension in {".txt", ".md"}:
        text = _decode_text(content)
    elif extension in {".html", ".htm"}:
        text = _extract_html_text(content)
    elif extension == ".rtf":
        text = _extract_rtf_text(content)
    elif extension == ".odt":
        text = _extract_odt_text(content)
    elif extension == ".doc":
        text = _extract_legacy_doc_text(content)
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


def _decode_text(content: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-16", "cp1251", "latin-1"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise UnreadableResumeError("Could not determine text encoding")


class _TextOnlyHtmlParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)


def _extract_html_text(content: bytes) -> str:
    parser = _TextOnlyHtmlParser()
    parser.feed(_decode_text(content))
    return "\n".join(parser.parts)


def _extract_rtf_text(content: bytes) -> str:
    source = content.decode("latin-1")
    source = re.sub(
        r"\\'[0-9a-fA-F]{2}",
        lambda match: bytes.fromhex(match.group()[2:]).decode("cp1251", errors="ignore"),
        source,
    )
    source = re.sub(r"\\u(-?\d+)\??", lambda match: chr(int(match.group(1)) % 65536), source)
    source = re.sub(r"\\(?:par|line)\b", "\n", source)
    source = re.sub(r"\\[a-zA-Z]+-?\d* ?", "", source)
    return source.replace("{", "").replace("}", "")


def _extract_odt_text(content: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            root = ElementTree.fromstring(archive.read("content.xml"))
    except (OSError, KeyError, zipfile.BadZipFile, ElementTree.ParseError) as error:
        raise UnreadableResumeError(f"Could not parse ODT: {error}") from error
    return "\n".join(part.strip() for part in root.itertext() if part.strip())


def _extract_legacy_doc_text(content: bytes) -> str:
    if not content.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        raise UnreadableResumeError("Could not parse DOC: invalid OLE signature")
    ascii_parts = [
        part.decode("cp1251", errors="ignore")
        for part in re.findall(rb"[\x20-\x7e\xc0-\xff]{5,}", content)
    ]
    unicode_parts = [
        part.decode("utf-16le", errors="ignore")
        for part in re.findall(rb"(?:[\x20-\x7e\xc0-\xff]\x00){5,}", content)
    ]
    return "\n".join(ascii_parts + unicode_parts)
