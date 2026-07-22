import json
from pathlib import Path

import pytest

from app.browser.selector_library import (
    InvalidSelectorLibrary,
    SelectorCandidate,
    SelectorKind,
    SelectorLibrary,
)


def test_selector_library_promotes_versions_and_keeps_rollback_history(tmp_path: Path) -> None:
    library = SelectorLibrary(tmp_path)

    first = library.promote("controlled", "email", SelectorCandidate(SelectorKind.LABEL, "Email"))
    second = library.promote("controlled", "email", SelectorCandidate(SelectorKind.ID, "email"))

    assert first.version == 1
    assert second.version == 2
    assert library.candidates("controlled", "email") == (
        SelectorCandidate(SelectorKind.ID, "email"),
    )
    versions = library.versions("controlled", "email")
    assert [version.is_active for version in versions] == [False, True]
    payload = json.loads((tmp_path / "selectors" / "controlled.json").read_text("utf-8"))
    assert payload["schema_version"] == 1


def test_selector_library_rejects_adapter_path_escape(tmp_path: Path) -> None:
    library = SelectorLibrary(tmp_path)

    with pytest.raises(InvalidSelectorLibrary, match="Adapter name is invalid"):
        library.candidates("../outside", "email")
