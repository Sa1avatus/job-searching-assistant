"""Annotation integrity: pair key, provenance, confidence and duplicate protection.

Revision ID: 0043
Revises: 0042
Create Date: 2026-09-18

Adds the canonical ``pair_key`` (smaller vacancy id first, so a pair has one identity
whatever order it was shown in), provenance (``annotator_id``, ``source``) and an optional
``confidence``. Partial unique indexes then make logical duplicates impossible.

Existing rows are normalised first (pairs re-ordered canonically, labels and reasons
flipped with them). If real duplicates already exist the migration stops instead of
deleting human labels: resolve them by hand and re-run.
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0043"
down_revision = "0042"
branch_labels = None
depends_on = None

_FLIP = {"a_better": "b_better", "b_better": "a_better"}


def _loads(value: object) -> list:
    if isinstance(value, str):
        return json.loads(value)
    return list(value or [])


def upgrade() -> None:
    op.add_column("annotation_feedback", sa.Column("pair_key", sa.String(80), nullable=True))
    op.add_column("annotation_feedback", sa.Column("confidence", sa.String(10), nullable=True))
    op.add_column("annotation_feedback", sa.Column("annotator_id", sa.String(36), nullable=True))
    op.add_column(
        "annotation_feedback",
        sa.Column("source", sa.String(30), nullable=False, server_default="dashboard"),
    )

    bind = op.get_bind()
    table = sa.table(
        "annotation_feedback",
        sa.column("id", sa.String),
        sa.column("user_id", sa.String),
        sa.column("feedback_type", sa.String),
        sa.column("label", sa.String),
        sa.column("vacancy_id", sa.String),
        sa.column("vacancy_a_id", sa.String),
        sa.column("vacancy_b_id", sa.String),
        sa.column("a_reasons", sa.JSON),
        sa.column("b_reasons", sa.JSON),
        sa.column("pair_key", sa.String),
        sa.column("annotator_id", sa.String),
    )
    for row in bind.execute(sa.select(table)).all():
        values: dict[str, object] = {"annotator_id": row.user_id}
        if row.feedback_type == "pairwise" and row.vacancy_a_id and row.vacancy_b_id:
            first, second = row.vacancy_a_id, row.vacancy_b_id
            label = row.label
            a_reasons, b_reasons = _loads(row.a_reasons), _loads(row.b_reasons)
            if first > second:
                first, second = second, first
                label = _FLIP.get(label, label)
                a_reasons, b_reasons = b_reasons, a_reasons
            values.update(
                vacancy_a_id=first,
                vacancy_b_id=second,
                vacancy_id=first,
                label=label,
                a_reasons=a_reasons,
                b_reasons=b_reasons,
                pair_key=f"{first}:{second}",
            )
        bind.execute(table.update().where(table.c.id == row.id).values(**values))

    duplicates = bind.execute(
        sa.text(
            "SELECT 'pointwise', COUNT(*) FROM ("
            " SELECT 1 FROM annotation_feedback WHERE feedback_type = 'pointwise'"
            " GROUP BY user_id, resume_id, vacancy_id HAVING COUNT(*) > 1) d "
            "UNION ALL SELECT 'pairwise', COUNT(*) FROM ("
            " SELECT 1 FROM annotation_feedback WHERE feedback_type = 'pairwise'"
            " GROUP BY user_id, resume_id, pair_key HAVING COUNT(*) > 1) d"
        )
    ).all()
    found = {kind: count for kind, count in duplicates if count}
    if found:
        raise RuntimeError(
            f"annotation_feedback already contains duplicate labels {found}; "
            "resolve them manually before applying migration 0043"
        )

    op.create_index(
        "uq_annotation_pointwise",
        "annotation_feedback",
        ["user_id", "resume_id", "vacancy_id"],
        unique=True,
        postgresql_where=sa.text("feedback_type = 'pointwise'"),
        sqlite_where=sa.text("feedback_type = 'pointwise'"),
    )
    op.create_index(
        "uq_annotation_pairwise",
        "annotation_feedback",
        ["user_id", "resume_id", "pair_key"],
        unique=True,
        postgresql_where=sa.text("feedback_type = 'pairwise'"),
        sqlite_where=sa.text("feedback_type = 'pairwise'"),
    )


def downgrade() -> None:
    op.drop_index("uq_annotation_pairwise", table_name="annotation_feedback")
    op.drop_index("uq_annotation_pointwise", table_name="annotation_feedback")
    op.drop_column("annotation_feedback", "source")
    op.drop_column("annotation_feedback", "annotator_id")
    op.drop_column("annotation_feedback", "confidence")
    op.drop_column("annotation_feedback", "pair_key")
