from ui_source import read_ui_source


def test_dashboard_has_an_analytics_panel_wired_to_the_crm_api() -> None:
    dashboard = read_ui_source("dashboard")

    assert 'data-menu="crm"' in dashboard and 'data-panel="crm"' in dashboard
    for element_id in ("crm-group-by", "crm-refresh", "crm-insights", "crm-table"):
        assert f'id="{element_id}"' in dashboard
    assert "/crm/funnel?group_by=" in dashboard and "/crm/insights" in dashboard
    assert "async function loadCrm()" in dashboard
    assert "'annotation', 'crm'];" in dashboard  # deep-linkable
    assert "не причинно-следственный вывод" in dashboard  # the caveat is shown to the user


def test_strategy_recommendations_need_an_explicit_decision_in_the_ui() -> None:
    dashboard = read_ui_source("dashboard")

    assert 'id="strategy-generate"' in dashboard and 'id="strategy-list"' in dashboard
    assert "/strategy/recommendations/generate" in dashboard
    assert "/strategy/recommendations/${item.id}/decision" in dashboard
    assert "['accept', 'Принять', 'primary'], ['reject', 'Отклонить']" in dashboard
    assert "ничего не изменится без вашего решения" in dashboard.lower() or (
        "ничего не меняется без вашего решения" in dashboard
    )
