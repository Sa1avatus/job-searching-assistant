from ui_source import read_ui_source


def test_detailed_matching_polling_treats_degraded_as_terminal() -> None:
    dashboard = read_ui_source("dashboard")

    assert "['scored', 'degraded', 'failed'].includes(data.status)" in dashboard
