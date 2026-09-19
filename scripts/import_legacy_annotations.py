"""Import human labels an older JSA version stored in the application timeline.

    python scripts/import_legacy_annotations.py --from-resume 47fd2e1d-... --to-resume cbdc3201-...
    python scripts/import_legacy_annotations.py --from-resume ... --to-resume ... --apply
    python scripts/import_legacy_annotations.py --to-resume ... --rollback

Without ``--apply`` it only reports what would be imported. Only labels given for ``--from-resume``
are imported, attached to ``--to-resume``; other resumes' labels are ignored. ``--rollback`` removes
exactly the rows this script wrote (source ``legacy_timeline``) for ``--to-resume``.
"""

from __future__ import annotations

import argparse
import json

from app.services.legacy_annotation_import import import_legacy_labels, rollback_legacy_labels
from app.storage.database import SessionFactory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--from-resume", help="resume id as recorded in the old events")
    parser.add_argument("--to-resume", required=True, help="existing resume the labels attach to")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args(argv)

    with SessionFactory() as session:
        if args.rollback:
            removed = rollback_legacy_labels(session, target_resume_id=args.to_resume)
            session.commit()
            print(json.dumps({"rolled_back": removed}))
            return 0
        if not args.from_resume:
            parser.error("--from-resume is required unless --rollback is used")
        report = import_legacy_labels(
            session,
            source_resume_id=args.from_resume,
            target_resume_id=args.to_resume,
            apply=args.apply,
        )
        if args.apply:
            session.commit()
        print(json.dumps(report.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
