from __future__ import annotations

from typing import TypedDict, cast

from playwright.async_api import Locator, Page

from app.domain.forms import FieldConstraints, FormField, FormFieldType


class FieldObservation(TypedDict):
    field_id: str
    label: str
    group_label: str
    tag_name: str
    input_type: str
    is_required: bool
    options: list[str]
    current_value: str | None
    name: str
    min_length: int | None
    max_length: int | None
    minimum: str | None
    maximum: str | None
    step: str | None
    pattern: str | None
    accepted_file_types: list[str]
    allows_multiple: bool


SEMANTIC_TERMS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("email", ("email", "e-mail")),
    ("phone", ("phone", "telephone", "mobile")),
    ("full_name", ("full name", "name")),
    ("resume", ("resume", "cv", "curriculum vitae")),
    ("salary", ("salary", "compensation", "pay")),
    ("experience", ("experience", "years worked", "years of work")),
    ("work_authorization", ("work authorization", "authorized to work", "visa")),
)


def classify_semantic_category(label: str, name: str) -> tuple[str, float]:
    searchable_text = f"{label} {name}".casefold()
    for category, terms in SEMANTIC_TERMS:
        if any(term in searchable_text for term in terms):
            return category, 0.95
    return "custom", 0.4 if label else 0.1


def classify_field_type(tag_name: str, input_type: str) -> FormFieldType:
    if tag_name == "textarea":
        return FormFieldType.TEXTAREA
    if tag_name == "select":
        return FormFieldType.SELECT
    typed_inputs = {
        "checkbox": FormFieldType.CHECKBOX,
        "date": FormFieldType.DATE,
        "file": FormFieldType.FILE,
        "number": FormFieldType.NUMBER,
        "radio": FormFieldType.RADIO,
    }
    if input_type in typed_inputs:
        return typed_inputs[input_type]
    if input_type in {"email", "password", "search", "tel", "text", "url"}:
        return FormFieldType.TEXT
    return FormFieldType.UNKNOWN


async def discover_form_fields(page: Page) -> tuple[FormField, ...]:
    controls = page.locator(
        "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset])"
        ":not([type=image]):not([disabled]), textarea:not([disabled]), select:not([disabled])"
    )
    observations = [
        await _observe_control(controls.nth(index)) for index in range(await controls.count())
    ]
    fields: list[FormField] = []
    processed_radio_names: set[str] = set()
    for index, observation in enumerate(observations):
        if observation["input_type"] == "radio" and observation["name"]:
            radio_name = observation["name"]
            if radio_name in processed_radio_names:
                continue
            processed_radio_names.add(radio_name)
            radio_observations = [
                candidate
                for candidate in observations
                if candidate["input_type"] == "radio" and candidate["name"] == radio_name
            ]
            observation = {
                **observation,
                "field_id": radio_name,
                "label": observation["group_label"] or observation["label"],
                "is_required": any(item["is_required"] for item in radio_observations),
                "options": [item["label"] for item in radio_observations if item["label"]],
                "current_value": next(
                    (
                        item["current_value"]
                        for item in radio_observations
                        if item["current_value"] is not None
                    ),
                    None,
                ),
            }
        fields.append(_to_form_field(observation, index))
    return tuple(fields)


async def _observe_control(control: Locator) -> FieldObservation:
    return cast(
        FieldObservation,
        await control.evaluate(
            r"""element => {
                const labelledBy = (element.getAttribute('aria-labelledby') || '')
                    .split(/\s+/).filter(Boolean)
                    .map(id => document.getElementById(id)?.textContent?.trim() || '')
                    .filter(Boolean).join(' ');
                const fieldsetLabel = element.closest('fieldset')
                    ?.querySelector(':scope > legend')?.textContent?.trim() || '';
                const label = element.labels?.[0]?.innerText?.trim()
                    || element.getAttribute('aria-label')?.trim()
                    || labelledBy
                    || element.getAttribute('placeholder')?.trim()
                    || fieldsetLabel;
                const inputType = (element.getAttribute('type') || 'text').toLowerCase();
                const isChoice = inputType === 'radio' || inputType === 'checkbox';
                return {
                    field_id: element.id || element.name || '',
                    label: label || '',
                    group_label: fieldsetLabel,
                    tag_name: element.tagName.toLowerCase(),
                    input_type: inputType,
                    is_required: element.required
                        || element.getAttribute('aria-required') === 'true',
                    options: element.tagName === 'SELECT'
                        ? Array.from(element.options).map(option => option.text.trim())
                        : [],
                    current_value: isChoice
                        ? (element.checked ? (label || element.value || 'true') : null)
                        : (element.value || null),
                    name: element.name || '',
                    min_length: element.minLength >= 0 ? element.minLength : null,
                    max_length: element.maxLength >= 0 ? element.maxLength : null,
                    minimum: element.getAttribute('min'),
                    maximum: element.getAttribute('max'),
                    step: element.getAttribute('step'),
                    pattern: element.getAttribute('pattern'),
                    accepted_file_types: (element.getAttribute('accept') || '')
                        .split(',').map(value => value.trim()).filter(Boolean),
                    allows_multiple: Boolean(element.multiple)
                };
            }"""
        ),
    )


def _to_form_field(observation: FieldObservation, index: int) -> FormField:
    semantic_category, confidence = classify_semantic_category(
        observation["label"], observation["name"]
    )
    field_id = observation["field_id"] or f"field-{index}"
    if observation["label"]:
        source_locator = f"label:{observation['label']}"
    elif observation["name"]:
        source_locator = f"name:{observation['name']}"
    else:
        source_locator = f"nth:{index}"
    return FormField(
        field_id=field_id,
        label=observation["label"],
        field_type=classify_field_type(observation["tag_name"], observation["input_type"]),
        is_required=observation["is_required"],
        options=tuple(observation["options"]),
        current_value=observation["current_value"],
        semantic_category=semantic_category,
        confidence=confidence,
        source_locator=source_locator,
        constraints=FieldConstraints(
            min_length=observation["min_length"],
            max_length=observation["max_length"],
            minimum=observation["minimum"],
            maximum=observation["maximum"],
            step=observation["step"],
            pattern=observation["pattern"],
            accepted_file_types=tuple(observation["accepted_file_types"]),
            allows_multiple=observation["allows_multiple"],
        ),
    )
