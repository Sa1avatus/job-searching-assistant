"""Unit tests for VacancyRow attribute storage metadata."""

from __future__ import annotations

from unittest.mock import MagicMock

import sqlalchemy as sa

from app.storage.tables import VacancyRow


def _col(name: str) -> sa.Column:
    """Return the named column from VacancyRow metadata."""
    return VacancyRow.__table__.columns[name]


def test_salary_text_column() -> None:
    col = _col("salary_text")
    assert isinstance(col.type, sa.Text)
    assert not col.nullable
    assert col.default.arg == ""


def test_work_format_column() -> None:
    col = _col("work_format")
    assert isinstance(col.type, sa.String)
    assert col.type.length == 30
    assert not col.nullable
    assert col.default.arg == "unspecified"
    assert col.index


def test_employment_types_column() -> None:
    col = _col("employment_types")
    assert isinstance(col.type, sa.JSON)
    assert not col.nullable
    assert callable(col.default.arg)
    assert col.default.arg(MagicMock()) == []


def test_work_format_index_exists() -> None:
    ix = next(
        (i for i in VacancyRow.__table__.indexes if i.name == "ix_vacancies_work_format"),
        None,
    )
    assert ix is not None
    assert [c.name for c in ix.columns] == ["work_format"]
