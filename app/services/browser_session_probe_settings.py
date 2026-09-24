"""Per-user, per-site override of how often the background worker checks whether a saved
browser session is still authenticated (see app.workers.browser_worker).

Absent a row, the built-in default applies: rarely for hh.ru/LinkedIn - a probe is itself
automated activity on an account those sites actively watch for - often for a user-defined site,
which carries no such risk.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.storage.tables import BrowserSessionProbeSettingRow

DEFAULT_BUILTIN_INTERVAL_SECONDS = 3600
DEFAULT_CUSTOM_INTERVAL_SECONDS = 60
MIN_INTERVAL_SECONDS = 30
MAX_INTERVAL_SECONDS = 86_400


class InvalidProbeInterval(ValueError):
    pass


def default_interval_seconds(*, is_builtin: bool) -> int:
    return DEFAULT_BUILTIN_INTERVAL_SECONDS if is_builtin else DEFAULT_CUSTOM_INTERVAL_SECONDS


def get_interval_seconds(session: Session, user_id: str, site_key: str, *, is_builtin: bool) -> int:
    row = session.get(BrowserSessionProbeSettingRow, (user_id, site_key))
    if row is not None:
        return row.interval_seconds
    return default_interval_seconds(is_builtin=is_builtin)


def set_interval_seconds(
    session: Session, user_id: str, site_key: str, interval_seconds: int
) -> None:
    if not (MIN_INTERVAL_SECONDS <= interval_seconds <= MAX_INTERVAL_SECONDS):
        raise InvalidProbeInterval(
            f"Интервал должен быть от {MIN_INTERVAL_SECONDS} до {MAX_INTERVAL_SECONDS} секунд"
        )
    row = session.get(BrowserSessionProbeSettingRow, (user_id, site_key))
    if row is None:
        session.add(
            BrowserSessionProbeSettingRow(
                user_id=user_id, site_key=site_key, interval_seconds=interval_seconds
            )
        )
    else:
        row.interval_seconds = interval_seconds
    session.commit()
