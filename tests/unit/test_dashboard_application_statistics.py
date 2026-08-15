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
    assert '<option value="rejected">Отклонена мной</option>' in dashboard
    assert '<option value="employer_rejected">Отказ работодателя</option>' in dashboard
    assert "rejected: 'Отклонена мной'" in dashboard
    assert "employer_rejected: 'Отказ работодателя'" in dashboard
    assert 'id="sync-application-statuses"' in dashboard
    assert "`/v1/users/${userId}/application-sync`" in dashboard
    assert "{method: 'POST', headers: headers(false)}" in dashboard
    assert 'id="sync-application-emails"' in dashboard
    assert "`/v1/users/${userId}/application-email-sync`" in dashboard
    assert 'id="import-application-emails"' in dashboard
    assert 'id="application-email-files"' in dashboard
    assert 'accept=".eml,.mbox,.zip,message/rfc822,application/zip"' in dashboard
    assert "`/v1/users/${userId}/application-email-import`" in dashboard
    assert "const form = new FormData();" in dashboard
    assert "form.append('files', file, file.name)" in dashboard
    assert "${summary.unknown} unknown" in dashboard
    assert "${summary.unmatched} unmatched" in dashboard
    assert "summary.status_updated" in dashboard


def test_search_result_overflow_menu_expands_matching_explanation() -> None:
    dashboard = (Path(__file__).parents[2] / "app" / "static" / "dashboard.html").read_text(
        encoding="utf-8"
    )
    search_renderer = dashboard.split("function renderResult(item)", 1)[1].split(
        "const vacancyStatusLabels", 1
    )[0]

    assert "const matchDetails = element('div', undefined, 'match-details')" in search_renderer
    assert "currentLanguage === 'en' ? 'Why it matches' : 'Почему подходит'" in search_renderer
    assert (
        "loadMatchDetails(item.application_id, matchDetails, matchDetailsButton)" in search_renderer
    )
    assert "moreMenu.append(matchDetailsButton, blacklist)" in search_renderer
    assert "card.append(actions, taskState, matchDetails)" in search_renderer
    assert "statusBadge.dataset.status" in search_renderer
    assert "cardCorner.append(statusBadge, createMatchMeter(item.match_score))" in search_renderer


def test_resume_keywords_editor_matches_summary_size_and_limit() -> None:
    dashboard = (Path(__file__).parents[2] / "app" / "static" / "dashboard.html").read_text(
        encoding="utf-8"
    )

    assert '<textarea id="summary-text" maxlength="2000"></textarea>' in dashboard
    assert '<textarea id="keywords-text" maxlength="2000"></textarea>' in dashboard
