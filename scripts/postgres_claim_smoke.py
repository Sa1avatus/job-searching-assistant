"""Prove that concurrent PostgreSQL workers cannot claim the same task."""

from __future__ import annotations

import argparse
import uuid

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker

from app.storage.tables import WorkflowTaskRow
from app.storage.task_repository import SqlTaskRepository


def run(database_url: str) -> None:
    engine = create_engine(database_url, pool_pre_ping=True)
    session_factory = sessionmaker(bind=engine, expire_on_commit=False)
    idempotency_key = f"concurrency-smoke:{uuid.uuid4()}"
    with session_factory() as session:
        task = WorkflowTaskRow(
            idempotency_key=idempotency_key,
            state="scheduled",
            priority=1_000_000,
        )
        session.add(task)
        session.commit()
        task_id = task.id

    try:
        with session_factory() as first_worker_session:
            locked_row = first_worker_session.scalar(
                select(WorkflowTaskRow).where(WorkflowTaskRow.id == task_id).with_for_update()
            )
            assert locked_row is not None
            with session_factory() as second_worker_session:
                skipped_claim = SqlTaskRepository(second_worker_session).claim_next(
                    worker="concurrent-2"
                )
            winning_claim = SqlTaskRepository(first_worker_session).claim_next(
                worker="concurrent-1"
            )

        assert skipped_claim is None
        assert winning_claim is not None and winning_claim.task_id == task_id
        print("PostgreSQL claim smoke passed: locked work was skipped and claimed exactly once")
    finally:
        with Session(engine) as session:
            row = session.get(WorkflowTaskRow, task_id)
            if row is not None:
                session.delete(row)
                session.commit()
        engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database-url", required=True)
    arguments = parser.parse_args()
    run(arguments.database_url)


if __name__ == "__main__":
    main()
