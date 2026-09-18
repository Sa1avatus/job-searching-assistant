"""Read a dashboard page together with the CSS and JS it links from ``/assets``."""

import re
from pathlib import Path

STATIC_ROOT = Path(__file__).parents[2] / "app" / "static"
_ASSET_REFERENCE = re.compile(r'(?:src|href)="/assets/([^"]+)"')


def read_ui_source(page: str) -> str:
    html = (STATIC_ROOT / f"{page}.html").read_text(encoding="utf-8")
    assets = [
        (STATIC_ROOT / "assets" / reference).read_text(encoding="utf-8")
        for reference in _ASSET_REFERENCE.findall(html)
    ]
    return "\n".join([html, *assets])
