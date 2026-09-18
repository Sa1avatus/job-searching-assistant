from ui_source import read_ui_source


def test_dashboard_exposes_private_email_integration_form() -> None:
    dashboard = read_ui_source("dashboard")

    assert 'id="email-integration-form"' in dashboard
    assert 'id="email-password" type="password"' in dashboard
    assert 'autocomplete="new-password"' in dashboard
    assert "password: password.value || null" in dashboard
    assert "`/v1/users/${userId}/email-integration`" in dashboard
    assert "async function loadEmailIntegration()" in dashboard
    assert "loadEmailIntegration()" in dashboard
    assert "imap.gmail.com" in dashboard
