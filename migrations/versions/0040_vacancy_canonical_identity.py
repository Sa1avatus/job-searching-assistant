"""Canonical vacancy identity columns (source key/id, canonical URL, fingerprint).

Revision ID: 0040
Revises: 0039
Create Date: 2026-09-18

Columns are nullable/defaulted and the indexes are non-unique on purpose: existing
databases may already hold logical duplicates and this migration must never merge or
delete rows that applications reference. New duplicates are prevented at write time
(app.services.vacancy_identity); existing ones are reported by
scripts/report_vacancy_duplicates.py.
"""

import sqlalchemy as sa
from alembic import op

from app.domain.vacancy_identity import canonicalize_vacancy_url, vacancy_fingerprint

revision = "0040"
down_revision = "0039"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "vacancies",
        sa.Column("source_key", sa.String(30), nullable=False, server_default="other"),
    )
    op.add_column("vacancies", sa.Column("source_id", sa.String(200), nullable=True))
    op.add_column("vacancies", sa.Column("canonical_url", sa.String(2000), nullable=True))
    op.add_column("vacancies", sa.Column("dedup_fingerprint", sa.String(40), nullable=True))

    bind = op.get_bind()
    vacancies = sa.table(
        "vacancies",
        sa.column("id", sa.String),
        sa.column("source_url", sa.Text),
        sa.column("adapter_name", sa.String),
        sa.column("company", sa.String),
        sa.column("title", sa.String),
        sa.column("location", sa.String),
        sa.column("source_key", sa.String),
        sa.column("source_id", sa.String),
        sa.column("canonical_url", sa.String),
        sa.column("dedup_fingerprint", sa.String),
    )
    rows = bind.execute(
        sa.select(
            vacancies.c.id,
            vacancies.c.source_url,
            vacancies.c.adapter_name,
            vacancies.c.company,
            vacancies.c.title,
            vacancies.c.location,
        )
    ).all()
    for row in rows:
        identity = canonicalize_vacancy_url(row.source_url or "", row.adapter_name or "generic")
        bind.execute(
            vacancies.update()
            .where(vacancies.c.id == row.id)
            .values(
                source_key=identity.source_key,
                source_id=identity.source_id,
                canonical_url=identity.canonical_url[:2000],
                dedup_fingerprint=vacancy_fingerprint(
                    row.company or "", row.title or "", row.location or ""
                ),
            )
        )

    op.create_index("ix_vacancies_source_key", "vacancies", ["source_key"])
    op.create_index("ix_vacancies_source_id", "vacancies", ["source_id"])
    op.create_index("ix_vacancies_canonical_url", "vacancies", ["canonical_url"])
    op.create_index("ix_vacancies_dedup_fingerprint", "vacancies", ["dedup_fingerprint"])


def downgrade() -> None:
    op.drop_index("ix_vacancies_dedup_fingerprint", table_name="vacancies")
    op.drop_index("ix_vacancies_canonical_url", table_name="vacancies")
    op.drop_index("ix_vacancies_source_id", table_name="vacancies")
    op.drop_index("ix_vacancies_source_key", table_name="vacancies")
    op.drop_column("vacancies", "dedup_fingerprint")
    op.drop_column("vacancies", "canonical_url")
    op.drop_column("vacancies", "source_id")
    op.drop_column("vacancies", "source_key")
