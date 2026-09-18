from pathlib import Path

from ui_source import read_ui_source

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_dashboard_uses_native_model_select() -> None:
    dashboard = read_ui_source("dashboard")

    assert '<select id="llm-matching-model">' in dashboard
    assert '<select id="llm-materials-model">' in dashboard
    assert 'list="llm-model-options"' not in dashboard
    assert '<datalist id="llm-model-options">' not in dashboard
    assert "modelSelect.replaceChildren()" in dashboard
    assert "modelSelect.append(new Option(model, model))" in dashboard
    assert "payload.models.includes(previousModel)" in dashboard
