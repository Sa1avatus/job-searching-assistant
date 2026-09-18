import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.main import app

STATIC_ROOT = Path(__file__).parents[2] / "app" / "static"
PAGES = ("dashboard.html", "review.html")
ASSET_REFERENCE = re.compile(r'(?:src|href)="(/assets/[^"]+)"')


@pytest.mark.parametrize("page", PAGES)
def test_page_has_no_inline_script_or_style(page: str) -> None:
    html = (STATIC_ROOT / page).read_text(encoding="utf-8")

    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html)
    assert "<style" not in html


@pytest.mark.parametrize("page", PAGES)
def test_page_scripts_are_classic_and_not_deferred(page: str) -> None:
    html = (STATIC_ROOT / page).read_text(encoding="utf-8")

    for tag in re.findall(r"<script\b[^>]*>", html):
        assert "type=" not in tag  # classic scripts keep the shared global scope
        assert "defer" not in tag and "async" not in tag


@pytest.mark.parametrize("page", PAGES)
def test_every_referenced_asset_exists_and_is_served(page: str) -> None:
    html = (STATIC_ROOT / page).read_text(encoding="utf-8")
    references = ASSET_REFERENCE.findall(html)
    assert references, f"{page} references no external assets"

    client = TestClient(app)
    for reference in references:
        assert (STATIC_ROOT / reference.removeprefix("/")).is_file()
        response = client.get(reference)
        assert response.status_code == 200, reference
        assert response.headers["cache-control"] == "no-cache"
        expected = "javascript" if reference.endswith(".js") else "text/css"
        assert expected in response.headers["content-type"], reference


def test_assets_mount_does_not_expose_html_pages() -> None:
    client = TestClient(app)

    assert client.get("/assets/dashboard_backup.html").status_code == 404
    assert client.get("/assets/../review.html").status_code == 404
