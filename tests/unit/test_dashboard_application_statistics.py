from pathlib import Path


def test_dashboard_loads_and_refreshes_application_statistics() -> None:
    dashboard = (Path(__file__).parents[2] / "app" / "static" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'id="application-statistics"' in dashboard
    assert "async function loadApplicationStatistics()" in dashboard
    assert "`/v1/users/${userId}/application-statistics`" in dashboard
    assert "await loadApplicationStatistics();" in dashboard
    assert "loadSiteDefinitionsForFields(), loadApplicationStatistics()" in dashboard
    assert "email_events: 'Emails processed'" in dashboard
    assert "email_rejections: 'Email rejections'" in dashboard
    assert "email_next_stages: 'Email next stages'" in dashboard
    assert 'id="sync-application-statuses"' in dashboard
    assert "`/v1/users/${userId}/application-sync`" in dashboard
    assert "{method: 'POST', headers: headers(false)}" in dashboard
