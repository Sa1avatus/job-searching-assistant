from __future__ import annotations

import json
from dataclasses import dataclass, fields
from pathlib import Path


class InvalidHeadHunterApplyProfile(ValueError):
    """Raised when the external HeadHunter browser profile is invalid."""


@dataclass(frozen=True, slots=True)
class HeadHunterApplyProfile:
    response_buttons: tuple[str, ...]
    already_applied_markers: tuple[str, ...]
    already_applied_texts: tuple[str, ...]
    cover_letter_editable_fields: tuple[str, ...]
    cover_letter_informers: tuple[str, ...]
    cover_letter_reveal_buttons: tuple[str, ...]
    cover_letter_save_buttons: tuple[str, ...]
    submit_buttons: tuple[str, ...]
    confirmation_markers: tuple[str, ...]
    cross_country_dialogs: tuple[str, ...]
    cross_country_headings: tuple[str, ...]
    cross_country_continue_selectors: tuple[str, ...]
    cross_country_continue_buttons: tuple[str, ...]


DEFAULT_HEADHUNTER_APPLY_PROFILE_PATH = (
    Path(__file__).resolve().parents[2] / "config" / "browser" / "headhunter_apply.json"
)


def load_headhunter_apply_profile(
    profile_path: Path = DEFAULT_HEADHUNTER_APPLY_PROFILE_PATH,
) -> HeadHunterApplyProfile:
    try:
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise InvalidHeadHunterApplyProfile(
            f"HeadHunter apply profile cannot be read: {profile_path}"
        ) from error
    if not isinstance(payload, dict) or payload.get("schema_version") != 1:
        raise InvalidHeadHunterApplyProfile(
            "HeadHunter apply profile must use schema_version 1"
        )

    profile_values: dict[str, tuple[str, ...]] = {}
    for profile_field in fields(HeadHunterApplyProfile):
        raw_values = payload.get(profile_field.name)
        if (
            not isinstance(raw_values, list)
            or not raw_values
            or any(not isinstance(value, str) or not value.strip() for value in raw_values)
        ):
            raise InvalidHeadHunterApplyProfile(
                f"HeadHunter apply profile field is invalid: {profile_field.name}"
            )
        profile_values[profile_field.name] = tuple(raw_values)
    return HeadHunterApplyProfile(**profile_values)
