from ui_source import read_ui_source


def test_detailed_matching_polling_treats_degraded_as_terminal() -> None:
    """"Переранжировать" waits for the workflow task itself to reach a terminal state
    (waitForMatchingTask, which only knows completed/failed/cancelled/interrupted - a
    degraded aggregate result still completes the task), then takes match-details'
    final_score unconditionally - a degraded result is picked up the same as a clean
    "scored" one, not treated as still-processing."""
    dashboard = read_ui_source("dashboard")

    assert "await waitForMatchingTask(task, () => {});" in dashboard
    assert (
        "const data = await asJson(await fetch(`/v1/applications/${appId}/match-details`"
        in dashboard
    )
