from ui_source import read_ui_source


def test_dashboard_renders_the_backend_session_state_machine() -> None:
    dashboard = read_ui_source("dashboard")

    for state in (
        "DISCONNECTED",
        "LOGIN_REQUIRED",
        "AUTHENTICATING",
        "AUTHENTICATED",
        "READY",
        "EXPIRED",
        "REAUTH_REQUIRED",
    ):
        assert f"{state}:" in dashboard
    assert "function browserSessionState(session)" in dashboard
    assert "session.state" in dashboard
    # the confirm/cancel buttons follow the state, not a frontend guess
    assert "confirmButton.hidden = !waiting;" in dashboard
    # a rejected confirm re-reads the real state instead of retrying blindly
    assert "loadBrowserSessionStatuses().catch(refreshError" in dashboard
