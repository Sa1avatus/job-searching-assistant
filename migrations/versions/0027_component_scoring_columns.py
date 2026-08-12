import sqlalchemy as sa
from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "application_match_results",
        sa.Column(
            "language_score",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
    )
    op.add_column(
        "application_match_results",
        sa.Column(
            "semantic_similarity",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
    )
    op.add_column(
        "application_match_results",
        sa.Column(
            "reranker_score",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
    )
    op.add_column(
        "application_match_results",
        sa.Column(
            "requirements_match",
            sa.Float(),
            server_default=sa.text("0.0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_application_match_results_language_range",
        "application_match_results",
        "language_score >= 0 AND language_score <= 100",
    )
    op.create_check_constraint(
        "ck_application_match_results_semantic_range",
        "application_match_results",
        "semantic_similarity >= 0 AND semantic_similarity <= 1",
    )
    op.create_check_constraint(
        "ck_application_match_results_reranker_range",
        "application_match_results",
        "reranker_score >= 0 AND reranker_score <= 1",
    )
    op.create_check_constraint(
        "ck_application_match_results_requirements_range",
        "application_match_results",
        "requirements_match >= 0 AND requirements_match <= 100",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_application_match_results_requirements_range",
        "application_match_results",
    )
    op.drop_constraint(
        "ck_application_match_results_reranker_range",
        "application_match_results",
    )
    op.drop_constraint(
        "ck_application_match_results_semantic_range",
        "application_match_results",
    )
    op.drop_constraint(
        "ck_application_match_results_language_range",
        "application_match_results",
    )
    op.drop_column("application_match_results", "requirements_match")
    op.drop_column("application_match_results", "reranker_score")
    op.drop_column("application_match_results", "semantic_similarity")
    op.drop_column("application_match_results", "language_score")
