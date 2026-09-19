from ui_source import read_ui_source


def test_dashboard_has_an_annotation_panel_wired_to_the_annotation_api() -> None:
    dashboard = read_ui_source("dashboard")

    assert 'data-menu="annotation"' in dashboard and 'data-panel="annotation"' in dashboard
    for element_id in (
        "annotation-resume",
        "annotation-mode",
        "annotation-load",
        "annotation-work",
        "annotation-report",
        "annotation-split-name",
        "annotation-create-split",
        "annotation-freeze-split",
    ):
        assert f'id="{element_id}"' in dashboard
    for endpoint in (
        "/v1/annotation/queue",
        "/v1/annotation/pair-queue",
        "/v1/annotation/discover",
        "/v1/annotation/dataset-report",
        "/v1/annotation/splits",
        "/v1/annotation/${path}",
    ):
        assert endpoint in dashboard
    assert "'annotation', 'crm'];" in dashboard  # deep-linkable via ?panel=annotation


def test_annotation_ui_hides_system_ranks_and_scores_from_the_reviewer() -> None:
    dashboard = read_ui_source("dashboard")
    card_source = dashboard[
        dashboard.index("function annotationCard(") : dashboard.index(
            "function annotationReasonPicker("
        )
    ]

    for hidden in ("current_rank", "ltr_rank", "current_score", "ltr_score", "review_priority"):
        assert (
            hidden not in card_source
        )  # anchoring the human on the system's opinion biases labels
    # ...while the context is still recorded with the label for later analysis
    assert "function samplingContext(item)" in dashboard


def test_freezing_asks_for_confirmation_because_it_is_irreversible() -> None:
    dashboard = read_ui_source("dashboard")

    assert "window.confirm(`Заморозить сплит" in dashboard
