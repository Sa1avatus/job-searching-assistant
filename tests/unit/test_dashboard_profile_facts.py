from ui_source import read_ui_source


def test_dashboard_exposes_editable_profile_facts_block() -> None:
    dashboard = read_ui_source("dashboard")

    assert 'id="profile-facts-panel"' in dashboard
    assert 'id="profile-fact-form"' in dashboard
    assert 'id="profile-facts-list"' in dashboard
    assert "async function loadProfileFacts()" in dashboard
    assert "`/v1/users/${userId}/facts`" in dashboard
    assert "`/v1/users/${userId}/facts/${factId}`" in dashboard
    assert "method: 'POST'" in dashboard
    assert "method: 'PUT'" in dashboard
    assert "method: 'DELETE'" in dashboard
    assert "const activeCv = cvs.find(cv => cv.is_active);" in dashboard
    assert "const cvName = activeCv.original_filename;" in dashboard
    assert "Извлечение фактов из «${cvName}»" in dashboard
    assert "const cvId = cvs[0].id" not in dashboard
