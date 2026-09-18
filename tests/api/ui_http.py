"""Fetch a served UI page together with the CSS/JS assets it links from ``/assets``."""

import re
from dataclasses import dataclass
from typing import Any

from fastapi.testclient import TestClient

_ASSET_REFERENCE = re.compile(r'(?:src|href)="(/assets/[^"]+)"')


@dataclass(frozen=True)
class UiPage:
    status_code: int
    headers: Any
    text: str


def get_ui_page(client: TestClient, path: str) -> UiPage:
    page = client.get(path)
    assets = []
    for reference in _ASSET_REFERENCE.findall(page.text):
        asset = client.get(reference)
        assert asset.status_code == 200, f"{reference} returned {asset.status_code}"
        assets.append(asset.text)
    return UiPage(page.status_code, page.headers, "\n".join([page.text, *assets]))
