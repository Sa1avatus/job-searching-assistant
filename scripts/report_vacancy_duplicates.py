"""Report (never merge) logical vacancy duplicates already stored in the database.

Two groups are listed:
  * identity duplicates - several rows with the same (source_key, source_id), i.e. the same
    posting stored under different URL spellings before canonical identity existed;
  * fingerprint groups - rows from different sources sharing company/title/location; these
    are only candidates, since a company can legitimately open several identical roles.

Read-only. Usage: python scripts/report_vacancy_duplicates.py
"""

from __future__ import annotations

from collections import defaultdict

from sqlalchemy import select

from app.storage.database import SessionFactory
from app.storage.tables import ApplicationRow, VacancyRow


def main() -> None:
    with SessionFactory() as session:
        rows = session.execute(
            select(
                VacancyRow.id,
                VacancyRow.source_key,
                VacancyRow.source_id,
                VacancyRow.dedup_fingerprint,
                VacancyRow.source_url,
                VacancyRow.company,
                VacancyRow.title,
            ).order_by(VacancyRow.created_at, VacancyRow.id)
        ).all()
        applications: dict[str, int] = defaultdict(int)
        for (vacancy_id,) in session.execute(select(ApplicationRow.vacancy_id)):
            applications[vacancy_id] += 1

    by_identity = defaultdict(list)
    by_fingerprint = defaultdict(list)
    for row in rows:
        if row.source_id:
            by_identity[(row.source_key, row.source_id)].append(row)
        if row.dedup_fingerprint:
            by_fingerprint[row.dedup_fingerprint].append(row)

    identity_dups = {k: v for k, v in by_identity.items() if len(v) > 1}
    print(f"vacancies: {len(rows)}")
    print(f"identity duplicate groups: {len(identity_dups)}")
    for (source_key, source_id), group in identity_dups.items():
        print(f"  {source_key}:{source_id}")
        for row in group:
            print(f"    {row.id}  applications={applications[row.id]}  {row.source_url}")

    cross = {
        k: v for k, v in by_fingerprint.items() if len(v) > 1 and len({r.source_key for r in v}) > 1
    }
    print(f"cross-source fingerprint candidate groups: {len(cross)}")
    for group in list(cross.values())[:50]:
        print(f"  {group[0].company} | {group[0].title}")
        for row in group:
            print(f"    {row.id}  {row.source_key}  {row.source_url}")


if __name__ == "__main__":
    main()
