from __future__ import annotations

import json
from pathlib import Path

import pytest

from adapters.job_boards.headhunter_apply_profile import (
    DEFAULT_HEADHUNTER_APPLY_PROFILE_PATH,
    InvalidHeadHunterApplyProfile,
    load_headhunter_apply_profile,
)


def test_default_headhunter_apply_profile_loads_current_browser_flow() -> None:
    profile = load_headhunter_apply_profile()

    assert "[data-qa='vacancy-response-letter-toggle']" in (profile.cover_letter_reveal_buttons)
    assert "Still apply" in profile.cross_country_continue_buttons
    assert "[role='alertdialog']" in profile.cross_country_dialogs
    assert "[data-qa='relocation-warning-confirm']" in (profile.cross_country_continue_selectors)
    assert profile.cover_letter_editable_fields
    assert DEFAULT_HEADHUNTER_APPLY_PROFILE_PATH.name == "headhunter_apply.json"


def test_headhunter_apply_profile_rejects_missing_selector_group(
    tmp_path: Path,
) -> None:
    payload = json.loads(DEFAULT_HEADHUNTER_APPLY_PROFILE_PATH.read_text(encoding="utf-8"))
    payload["submit_buttons"] = []
    profile_path = tmp_path / "headhunter_apply.json"
    profile_path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(
        InvalidHeadHunterApplyProfile,
        match="submit_buttons",
    ):
        load_headhunter_apply_profile(profile_path)
