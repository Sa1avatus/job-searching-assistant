from io import BytesIO
from zipfile import ZipFile

from app.domain.resume_text import extract_resume_text

RESUME_TEXT = "Senior Python engineer with distributed systems and database experience."


def test_extracts_plain_text_markdown_and_html() -> None:
    assert "Python engineer" in extract_resume_text(RESUME_TEXT.encode(), extension=".txt")
    assert "Python engineer" in extract_resume_text(
        f"# Profile\n{RESUME_TEXT}".encode(), extension=".md"
    )
    assert "Python engineer" in extract_resume_text(
        f"<html><body><p>{RESUME_TEXT}</p></body></html>".encode(), extension=".html"
    )


def test_extracts_rtf_and_odt() -> None:
    rtf = ("{\\rtf1\\ansi " + RESUME_TEXT + "}").encode()
    assert "Python engineer" in extract_resume_text(rtf, extension=".rtf")

    stream = BytesIO()
    with ZipFile(stream, "w") as archive:
        archive.writestr(
            "content.xml",
            '<office:text xmlns:office="urn:o"><text:p xmlns:text="urn:t">'
            f"{RESUME_TEXT}</text:p></office:text>",
        )
    assert "Python engineer" in extract_resume_text(stream.getvalue(), extension=".odt")


def test_extracts_recoverable_text_from_legacy_doc() -> None:
    content = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 32 + RESUME_TEXT.encode()

    assert "Python engineer" in extract_resume_text(content, extension=".doc")
