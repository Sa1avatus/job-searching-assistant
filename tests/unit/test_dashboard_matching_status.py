from pathlib import Path


def test_detailed_matching_polling_treats_degraded_as_terminal() -> None:
    dashboard = (Path(__file__).parents[2] / "app" / "static" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert "['scored', 'degraded', 'failed'].includes(data.status)" in dashboard
