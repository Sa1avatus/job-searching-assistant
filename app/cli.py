from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Protocol, cast

from alembic import command
from alembic.config import Config
from sqlalchemy import select

from app.config import get_settings
from app.domain.models import ProfileFact, Vacancy
from app.domain.policy import assess_vacancy
from app.matching.backfill import MatchingBackfillService
from app.services.rag_sync import RagSyncService
from app.services.recruitment import RecruitmentService
from app.storage.database import SessionFactory
from app.storage.tables import WorkflowTaskRow
from app.workers.dispatcher import main as run_dispatcher


class CommandHandler(Protocol):
    def __call__(self, args: argparse.Namespace) -> int: ...


def _load_profile(path: Path) -> list[ProfileFact]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    return [ProfileFact(**fact) for fact in payload["facts"]]


def _assess(args: argparse.Namespace) -> int:
    facts = _load_profile(args.profile)
    vacancy_payload = json.loads(args.vacancy.read_text(encoding="utf-8"))
    vacancy = Vacancy(
        source_url=vacancy_payload["source_url"],
        title=vacancy_payload["title"],
        company=vacancy_payload["company"],
        required_skills=frozenset(vacancy_payload.get("required_skills", [])),
        preferred_skills=frozenset(vacancy_payload.get("preferred_skills", [])),
    )
    print(json.dumps(asdict(assess_vacancy(vacancy, facts)), indent=2))
    return 0


def _init_db(_args: argparse.Namespace) -> int:
    command.upgrade(Config("alembic.ini"), "head")
    print("Database migrations applied")
    return 0


def _import_profile(args: argparse.Namespace) -> int:
    facts = _load_profile(args.profile)
    with SessionFactory() as session:
        service = RecruitmentService(session)
        user = service.create_user(args.display_name)
        for fact in facts:
            service.add_profile_fact(
                user.id,
                category=fact.category,
                name=fact.name,
                value=fact.value,
                is_verified=fact.is_verified,
            )
    print(json.dumps({"user_id": user.id, "imported_facts": len(facts)}))
    return 0


def _add_vacancy(args: argparse.Namespace) -> int:
    with SessionFactory() as session:
        vacancy = RecruitmentService(session).create_vacancy(
            source_url=args.url,
            title=args.title,
            company=args.company,
            required_skills=args.required_skill,
            preferred_skills=args.preferred_skill,
        )
    print(json.dumps({"vacancy_id": vacancy.id, "source_url": vacancy.source_url}))
    return 0


def _list_tasks(_args: argparse.Namespace) -> int:
    with SessionFactory() as session:
        tasks = session.scalars(select(WorkflowTaskRow).order_by(WorkflowTaskRow.created_at)).all()
    print(
        json.dumps(
            [
                {
                    "idempotency_key": task.idempotency_key,
                    "state": task.state,
                    "attempt_number": task.attempt_number,
                }
                for task in tasks
            ],
            indent=2,
        )
    )
    return 0


def _review_applications(_args: argparse.Namespace) -> int:
    with SessionFactory() as session:
        review_items = RecruitmentService(session).list_review_queue()
    print(
        json.dumps(
            [
                {
                    "application_id": application.id,
                    "company": vacancy.company,
                    "vacancy_title": vacancy.title,
                    "source_url": vacancy.source_url,
                    "match_score": application.match_score,
                    "warnings": application.warnings,
                    "current_workflow_state": workflow_task.state,
                    "selected_cv_filename": (
                        cv_file.original_filename if cv_file is not None else None
                    ),
                }
                for application, vacancy, cv_file, workflow_task in review_items
            ],
            indent=2,
        )
    )
    return 0


def _run_worker(_args: argparse.Namespace) -> int:
    run_dispatcher()
    return 0


def _rag_backfill(args: argparse.Namespace) -> int:
    try:
        with SessionFactory() as session:
            result = asyncio.run(
                RagSyncService(session, get_settings()).backfill_owner(
                    args.user_id,
                    batch_size=args.batch_size,
                    after_resume_id=args.after_resume_id,
                    after_vacancy_id=args.after_vacancy_id,
                    force=args.force,
                )
            )
    except ValueError as error:
        print(json.dumps({"status": "invalid_request", "detail": str(error)}))
        return 2
    print(json.dumps(result.as_dict(), indent=2))
    return 0


def _matching_backfill(args: argparse.Namespace) -> int:
    try:
        with SessionFactory() as session:
            result = MatchingBackfillService(session).schedule_stale_owner_results(
                args.user_id,
                batch_size=args.batch_size,
                after_application_id=args.after_application_id,
            )
    except ValueError as error:
        print(json.dumps({"status": "invalid_request", "detail": str(error)}))
        return 2
    print(json.dumps(result.as_dict(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="recruitment-assistant")
    commands = parser.add_subparsers(required=True)
    assess = commands.add_parser("assess", help="Assess a vacancy against verified profile facts")
    assess.add_argument("--profile", type=Path, required=True)
    assess.add_argument("--vacancy", type=Path, required=True)
    assess.set_defaults(handler=_assess)

    init_db = commands.add_parser("init-db", help="Apply database migrations")
    init_db.set_defaults(handler=_init_db)

    import_profile = commands.add_parser("import-profile", help="Import verified profile facts")
    import_profile.add_argument("profile", type=Path)
    import_profile.add_argument("--display-name", required=True)
    import_profile.set_defaults(handler=_import_profile)

    add_vacancy = commands.add_parser("add-vacancy", help="Persist a vacancy")
    add_vacancy.add_argument("url")
    add_vacancy.add_argument("--title", required=True)
    add_vacancy.add_argument("--company", required=True)
    add_vacancy.add_argument("--required-skill", action="append", default=[])
    add_vacancy.add_argument("--preferred-skill", action="append", default=[])
    add_vacancy.set_defaults(handler=_add_vacancy)

    list_tasks = commands.add_parser("list-tasks", help="List durable workflow tasks")
    list_tasks.set_defaults(handler=_list_tasks)

    review = commands.add_parser("review-applications", help="List applications awaiting review")
    review.set_defaults(handler=_review_applications)

    worker = commands.add_parser("run-worker", help="Run the durable Redis-coordinated dispatcher")
    worker.set_defaults(handler=_run_worker)

    rag_backfill = commands.add_parser(
        "rag-backfill",
        help="Synchronize one owner's profile, resumes, and vacancies with RAG",
    )
    rag_backfill.add_argument("--user-id", required=True)
    rag_backfill.add_argument("--batch-size", type=int, default=50, choices=range(1, 101))
    rag_backfill.add_argument("--after-resume-id")
    rag_backfill.add_argument("--after-vacancy-id")
    rag_backfill.add_argument(
        "--force", action="store_true", help="Re-index already processed documents"
    )
    rag_backfill.set_defaults(handler=_rag_backfill)

    matching_backfill = commands.add_parser(
        "matching-backfill",
        help="Schedule stale matching explanations for one owner",
    )
    matching_backfill.add_argument("--user-id", required=True)
    matching_backfill.add_argument("--batch-size", type=int, default=50, choices=range(1, 101))
    matching_backfill.add_argument("--after-application-id")
    matching_backfill.set_defaults(handler=_matching_backfill)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handler = cast(CommandHandler, args.handler)
    return handler(args)


if __name__ == "__main__":
    raise SystemExit(main())
