"""Add salary, work_format, and employment_types to vacancies.

Revision ID: 0018
Revises: 0017
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

from app.domain.vacancy_attributes import detect_employment_types, find_salary_text
from app.services.vacancy_metadata import detect_work_format

revision = "0018"
down_revision = "0017"


def upgrade() -> None:
    with op.batch_alter_table("vacancies") as batch:
        batch.add_column(
            sa.Column(
                "salary_text",
                sa.Text(),
                nullable=False,
                server_default=sa.text("''"),
            )
        )
        batch.add_column(
            sa.Column(
                "work_format",
                sa.String(30),
                nullable=False,
                server_default=sa.text("'unspecified'"),
            )
        )
        batch.add_column(
            sa.Column(
                "employment_types",
                sa.JSON(),
                nullable=False,
                server_default=sa.text("'[]'"),
            )
        )
        batch.create_index("ix_vacancies_work_format", ["work_format"])

    vacancies = sa.table(
        "vacancies",
        sa.column("id", sa.String()),
        sa.column("title", sa.String()),
        sa.column("location", sa.String()),
        sa.column("description_text", sa.Text()),
        sa.column("salary_text", sa.Text()),
        sa.column("work_format", sa.String()),
        sa.column("employment_types", sa.JSON()),
    )
    connection = op.get_bind()
    existing_vacancies = connection.execute(
        sa.select(
            vacancies.c.id,
            vacancies.c.title,
            vacancies.c.location,
            vacancies.c.description_text,
        )
    ).mappings()
    for vacancy in existing_vacancies:
        title = vacancy["title"] or ""
        location = vacancy["location"] or ""
        description_text = vacancy["description_text"] or ""
        connection.execute(
            vacancies.update()
            .where(vacancies.c.id == vacancy["id"])
            .values(
                salary_text=find_salary_text(description_text),
                work_format=detect_work_format(title, location, description_text),
                employment_types=list(detect_employment_types(title, location, description_text)),
            )
        )

    with op.batch_alter_table("vacancies") as batch:
        batch.alter_column("salary_text", server_default=None)
        batch.alter_column("work_format", server_default=None)
        batch.alter_column("employment_types", server_default=None)


def downgrade() -> None:
    with op.batch_alter_table("vacancies") as batch:
        batch.drop_index("ix_vacancies_work_format")
        batch.drop_column("employment_types")
        batch.drop_column("work_format")
        batch.drop_column("salary_text")
