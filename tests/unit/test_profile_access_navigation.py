from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_renames_profile_access_without_changing_panel_routing() -> None:
    dashboard = (PROJECT_ROOT / "app" / "static" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert 'data-menu="access">Профиль и доступ</button>' in dashboard
    access_heading = (
        '<section class="step" data-panel="access">\n      <h2>Профиль и доступ</h2>'
    )
    assert access_heading in dashboard
    assert "'Профиль и доступ': 'Profile and access'" in dashboard
    assert dashboard.count("разделе «Профиль и доступ»") == 2
    assert "'access'" in dashboard
    assert 'data-menu="access">Доступ</button>' not in dashboard
    assert '<h2>Доступ</h2>' not in dashboard


def test_review_renames_profile_access_without_changing_link() -> None:
    review = (PROJECT_ROOT / "app" / "static" / "review.html").read_text(encoding="utf-8")

    assert (
        '<a id="nav-access" href="/dashboard?panel=access">Профиль и доступ</a>' in review
    )
    assert "tr('Профиль и доступ', 'Profile and access')" in review
    assert 'id="nav-access"' in review
    assert 'href="/dashboard?panel=access"' in review
    assert '>Доступ</a>' not in review
    assert "tr('Доступ', 'Access')" not in review


def test_dashboard_offers_custom_openai_compatible_provider() -> None:
    dashboard = (PROJECT_ROOT / "app" / "static" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert '<option value="openai_compatible">OpenAI-compatible</option>' in dashboard
    assert 'id="llm-base-url"' in dashboard
    assert "provider === 'openai_compatible'" in dashboard
    assert "base_url: baseUrl || null" in dashboard
