from ui_source import read_ui_source


def test_dashboard_edits_search_preferences_through_the_preferences_api() -> None:
    dashboard = read_ui_source("dashboard")

    for element_id in (
        "search-preferences-panel",
        "pref-min-salary",
        "pref-currency",
        "pref-locations",
        "save-search-preferences",
        "search-preferences-state",
    ):
        assert f'id="{element_id}"' in dashboard
    assert 'name="pref-work-format"' in dashboard
    assert 'name="pref-employment"' in dashboard
    assert "async function loadSearchPreferences()" in dashboard
    assert "`/v1/users/${userId}/preferences`" in dashboard
    assert "method: 'PUT'" in dashboard
    # loaded with the rest of the workspace whenever the user changes
    assert "loadEmailReview(), loadSearchPreferences()" in dashboard
