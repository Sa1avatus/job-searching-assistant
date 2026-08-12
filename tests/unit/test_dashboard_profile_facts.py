from pathlib import Path


def test_dashboard_exposes_editable_profile_facts_block() -> None:
    dashboard = (Path(__file__).parents[2] / "app" / "static" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'id="profile-facts-panel"' in dashboard
    assert 'id="profile-fact-form"' in dashboard
    assert 'id="profile-facts-list"' in dashboard
    assert "async function loadProfileFacts()" in dashboard
    assert "`/v1/users/${userId}/facts`" in dashboard
    assert "`/v1/users/${userId}/facts/${factId}`" in dashboard
    assert "method: 'POST'" in dashboard
    assert "method: 'PUT'" in dashboard
    assert "method: 'DELETE'" in dashboard
