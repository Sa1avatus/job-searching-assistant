from app.browser.search_reach_recording import RecordedAction, selector_candidates_for
from app.domain.workflow_selectors import WorkflowSelectorCandidate


def test_from_payload_reads_known_fields_and_ignores_extra_ones() -> None:
    action = RecordedAction.from_payload(
        {
            "kind": "fill",
            "tag": "input",
            "type": "text",
            "id": "q",
            "name": "query",
            "role": "",
            "ariaLabel": "",
            "testId": "",
            "placeholder": "Search jobs",
            "labelText": "",
            "text": "",
            "value": "python developer",
            "valueLength": 16,
            "somethingUnexpected": "ignored",
        }
    )

    assert action.kind == "fill"
    assert action.element_id == "q"
    assert action.name == "query"
    assert action.placeholder == "Search jobs"
    assert action.value_preview == "python developer"
    assert action.value_length == 16


def test_description_never_falls_back_to_the_typed_value() -> None:
    action = RecordedAction.from_payload(
        {"kind": "fill", "tag": "input", "value": "secret query text"}
    )

    assert "secret query text" not in action.description()
    assert action.description() == "input"


def test_selector_candidates_prefer_stable_identifiers_over_free_text() -> None:
    action = RecordedAction.from_payload(
        {
            "kind": "click",
            "tag": "button",
            "testId": "search-submit",
            "id": "go",
            "name": "",
            "role": "button",
            "ariaLabel": "Search",
            "labelText": "",
            "placeholder": "",
            "text": "Search",
        }
    )

    candidates = selector_candidates_for(action)

    assert [candidate.kind for candidate in candidates] == ["test_id", "id", "label", "role"]
    assert candidates[0].value == "search-submit"
    assert candidates[2].value == "Search"


def test_selector_candidates_is_empty_when_nothing_identifies_the_element() -> None:
    action = RecordedAction.from_payload({"kind": "click", "tag": "div"})

    assert selector_candidates_for(action) == []


def test_selector_candidates_falls_back_to_exact_text_for_a_bare_click() -> None:
    action = RecordedAction.from_payload({"kind": "click", "tag": "button", "text": "SEARCH JOBS"})

    candidates = selector_candidates_for(action)

    assert [(candidate.kind, candidate.value) for candidate in candidates] == [
        ("css", 'button:text-is("SEARCH JOBS")')
    ]


def test_selector_candidates_text_fallback_escapes_quotes() -> None:
    action = RecordedAction.from_payload({"kind": "click", "tag": "li", "text": 'Say "hi"'})

    candidates = selector_candidates_for(action)

    assert candidates == [WorkflowSelectorCandidate(kind="css", value='li:text-is("Say \\"hi\\"")')]


def test_selector_candidates_never_uses_the_text_fallback_for_a_fill_action() -> None:
    action = RecordedAction.from_payload(
        {"kind": "fill", "tag": "input", "text": "python developer"}
    )

    assert selector_candidates_for(action) == []
