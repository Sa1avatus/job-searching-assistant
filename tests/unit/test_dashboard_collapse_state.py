from ui_source import read_ui_source


def test_top_level_tabs_and_panel_titles_do_not_collapse() -> None:
    dashboard = read_ui_source("dashboard")

    # clicking the active menu tab again keeps the panel open
    assert "activatePanel(button.dataset.menu); // top-level tabs never collapse" in dashboard
    assert "activatePanel(button.dataset.menu, {toggle: true})" not in dashboard
    # the panel title (h2) is never turned into a collapsible heading
    assert (
        "if (child.matches('h2')) return; // top level: the panel title is always open" in dashboard
    )


def test_second_level_and_below_stay_collapsible_and_remember_their_state() -> None:
    dashboard = read_ui_source("dashboard")

    assert "dashboard-subsection-toggle" in dashboard
    assert "const SUBSECTION_STATE_KEY = 'dashboardSubsectionState';" in dashboard
    assert "saveSubsectionState(heading.dataset.stateKey, shouldOpen);" in dashboard
    assert "applyStoredSubsectionState();" in dashboard
    assert "collapseAllDashboardSubsections" not in dashboard  # no longer wipes the saved choice
    # keys do not depend on the interface language: panel name + position
    assert "heading.dataset.stateKey = `${panel}:${position}`;" in dashboard


def test_unreadable_storage_falls_back_to_collapsed() -> None:
    dashboard = read_ui_source("dashboard")
    reader = dashboard[
        dashboard.index("function readSubsectionState()") : dashboard.index(
            "function saveSubsectionState("
        )
    ]

    assert "try {" in reader and "catch" in reader and "return {};" in reader
