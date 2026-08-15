from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_renames_profile_access_without_changing_panel_routing() -> None:
    dashboard = (PROJECT_ROOT / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")

    assert 'data-menu="access"' in dashboard
    assert "Профиль и доступ</button>" in dashboard
    access_heading = '<section class="step" data-panel="access">\n      <h2>Профиль и доступ</h2>'
    assert access_heading in dashboard
    assert "'Профиль и доступ': 'Profile and access'" in dashboard
    assert dashboard.count("разделе «Профиль и доступ»") == 2
    assert "'access'" in dashboard
    assert 'data-menu="access">Доступ</button>' not in dashboard
    assert "<h2>Доступ</h2>" not in dashboard


def test_review_renames_profile_access_without_changing_link() -> None:
    review = (PROJECT_ROOT / "app" / "static" / "review.html").read_text(encoding="utf-8")

    assert '<a id="nav-access" href="/dashboard?panel=access">Профиль и доступ</a>' in review
    assert "tr('Профиль и доступ', 'Profile and access')" in review
    assert 'id="nav-access"' in review
    assert 'href="/dashboard?panel=access"' in review
    assert ">Доступ</a>" not in review
    assert "tr('Доступ', 'Access')" not in review


def test_dashboard_offers_custom_openai_compatible_provider() -> None:
    dashboard = (PROJECT_ROOT / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")

    assert '<option value="openai_compatible">OpenAI-compatible</option>' in dashboard
    assert 'id="llm-base-url"' in dashboard
    assert "provider === 'openai_compatible'" in dashboard
    assert "base_url: baseUrl || null" in dashboard


def test_dashboard_personal_data_uses_canonical_autofill_api() -> None:
    dashboard = (PROJECT_ROOT / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")

    for field_id in (
        "profile-full-name",
        "profile-first-name",
        "profile-last-name",
        "profile-email",
        "profile-phone",
        "profile-linkedin",
        "profile-country",
        "profile-city",
    ):
        assert f'id="{field_id}"' in dashboard
    for key in (
        "identity.full_name",
        "identity.first_name",
        "identity.last_name",
        "contact.email",
        "contact.phone",
        "contact.linkedin_url",
        "location.country",
        "location.city",
    ):
        assert key in dashboard
    assert "method: 'POST'" in dashboard
    assert "method: 'PUT'" in dashboard
    assert "method: 'DELETE'" in dashboard
    assert "loadAutofillValues()" in dashboard
    assert 'id="store-sensitive-personal-data"' in dashboard
    assert "value.requires_review" in dashboard
    assert "field.is_sensitive" in dashboard
    assert "Подтвердите зашифрованное хранение чувствительных данных" in dashboard


def test_dashboard_application_defaults_use_canonical_keys() -> None:
    dashboard = (PROJECT_ROOT / "app" / "static" / "dashboard.html").read_text(encoding="utf-8")

    for field_id in (
        "application-salary",
        "application-currency",
        "application-notice-period",
        "application-relocation",
    ):
        assert f'id="{field_id}"' in dashboard
    for key in (
        "job_preferences.expected_salary",
        "job_preferences.currency",
        "job_preferences.notice_period",
        "job_preferences.relocation_ready",
    ):
        assert key in dashboard
    assert "saveAutofillFields(applicationAutofillFields)" in dashboard
