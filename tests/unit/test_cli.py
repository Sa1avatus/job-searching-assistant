from app.cli import build_parser


def test_cli_exposes_required_operational_commands() -> None:
    parser = build_parser()
    help_text = parser.format_help()

    assert "init-db" in help_text
    assert "import-profile" in help_text
    assert "add-vacancy" in help_text
    assert "list-tasks" in help_text
    assert "review-applications" in help_text
