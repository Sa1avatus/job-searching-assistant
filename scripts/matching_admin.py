from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict

import httpx

from app.config import get_settings
from app.matching.backfill import MatchingBackfillService
from app.matching.http_models import HttpEmbeddingClient
from app.matching.indexing import EvidenceReindexService
from app.matching.opensearch_index import OpenSearchEvidenceIndex
from app.matching.runtime import MatchingRuntime
from app.storage.database import SessionFactory


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Bounded matching v2 operations")
    subparsers = parser.add_subparsers(dest="command", required=True)

    for name in (
        "extract-requirements",
        "extract-evidence",
        "recalculate-all",
    ):
        command = subparsers.add_parser(name)
        _add_batch_arguments(command)

    rescore = subparsers.add_parser("rescore-application")
    rescore.add_argument("application_id")
    rescore.add_argument("--dry-run", action="store_true")

    for name in ("reindex-user", "reindex-cv"):
        command = subparsers.add_parser(name)
        command.add_argument("entity_id")
        command.add_argument("--dry-run", action="store_true")

    rebuild = subparsers.add_parser("rebuild-index")
    rebuild.add_argument("--dry-run", action="store_true")

    verify = subparsers.add_parser("verify-index-consistency")
    verify.add_argument("--user-id")
    verify.add_argument("--cv-file-id")
    verify.add_argument("--failure-limit", type=int, default=100)
    return parser


def _add_batch_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--resume-after")
    parser.add_argument("--user-id")
    parser.add_argument("--cv-file-id")


async def _reindex(*, user_id: str | None = None, cv_file_id: str | None = None) -> int:
    settings = get_settings()
    with SessionFactory() as session:
        async with (
            httpx.AsyncClient(
                base_url=settings.matching_model_service_url,
                timeout=settings.matching_model_timeout_seconds,
                trust_env=False,
            ) as model_http,
            httpx.AsyncClient(
                base_url=settings.opensearch_url,
                timeout=30,
                trust_env=False,
            ) as opensearch_http,
        ):
            service = EvidenceReindexService(
                session,
                HttpEmbeddingClient(model_http, dimensions=settings.embedding_dimensions),
                OpenSearchEvidenceIndex(
                    opensearch_http,
                    index_prefix=settings.opensearch_evidence_index_prefix,
                    read_alias=settings.opensearch_evidence_read_alias,
                    write_alias=settings.opensearch_evidence_write_alias,
                    dimensions=settings.embedding_dimensions,
                ),
            )
            if cv_file_id is not None:
                if user_id is None:
                    raise ValueError("user_id is required when reindexing one CV")
                return await service.index_cv(user_id=user_id, cv_file_id=cv_file_id)
            if user_id is not None:
                return await service.index_user(user_id=user_id)
            _, count = await service.rebuild()
            return count


def main() -> None:
    arguments = _parser().parse_args()
    if arguments.command in {
        "extract-requirements",
        "extract-evidence",
        "recalculate-all",
    }:
        with SessionFactory() as session:
            report = MatchingBackfillService(session).schedule_applications(
                dry_run=arguments.dry_run,
                limit=arguments.limit,
                batch_size=arguments.batch_size,
                resume_after=arguments.resume_after,
                user_id=arguments.user_id,
                cv_file_id=arguments.cv_file_id,
            )
        print(json.dumps(asdict(report), ensure_ascii=False))
        return
    if arguments.command == "rescore-application":
        if arguments.dry_run:
            print(json.dumps({"application_id": arguments.application_id, "dry_run": True}))
        else:
            asyncio.run(
                MatchingRuntime(SessionFactory, get_settings()).run(arguments.application_id)
            )
            print(json.dumps({"application_id": arguments.application_id, "status": "scored"}))
        return
    if arguments.command == "verify-index-consistency":
        with SessionFactory() as session:
            consistency_report = MatchingBackfillService(session).verify_index_metadata(
                user_id=arguments.user_id,
                cv_file_id=arguments.cv_file_id,
                failure_limit=arguments.failure_limit,
            )
        print(json.dumps(asdict(consistency_report), ensure_ascii=False))
        return
    if arguments.dry_run:
        print(json.dumps({"command": arguments.command, "dry_run": True}))
        return
    if arguments.command == "rebuild-index":
        count = asyncio.run(_reindex())
    elif arguments.command == "reindex-cv":
        with SessionFactory() as session:
            from app.storage.tables import CvFileRow

            cv_file = session.get(CvFileRow, arguments.entity_id)
            if cv_file is None:
                raise LookupError("CV file not found")
            user_id = cv_file.user_id
        count = asyncio.run(_reindex(user_id=user_id, cv_file_id=arguments.entity_id))
    else:
        count = asyncio.run(_reindex(user_id=arguments.entity_id))
    print(json.dumps({"command": arguments.command, "indexed": count}))


if __name__ == "__main__":
    main()
