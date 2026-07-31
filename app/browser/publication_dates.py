from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

_RUSSIAN_MONTHS = {
    "января": 1,
    "февраля": 2,
    "марта": 3,
    "апреля": 4,
    "мая": 5,
    "июня": 6,
    "июля": 7,
    "августа": 8,
    "сентября": 9,
    "октября": 10,
    "ноября": 11,
    "декабря": 12,
}


def parse_publication_datetime(
    value: str | None, *, now: datetime | None = None
) -> datetime | None:
    """Parse ISO, explicit Russian/English dates, and common relative posting labels."""
    if not value or not value.strip():
        return None
    text = " ".join(value.strip().casefold().split())
    reference = now or datetime.now(UTC)

    try:
        parsed = datetime.fromisoformat(text.replace("z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    except ValueError:
        pass

    if "сегодня" in text or text == "today":
        return reference
    if "вчера" in text or text == "yesterday":
        return reference - timedelta(days=1)

    relative = re.search(
        r"(\d+)\s*(minute|hour|day|week|month|минут|час|дн|день|дня|дней|недел|месяц)",
        text,
    )
    if relative:
        amount = int(relative.group(1))
        unit = relative.group(2)
        if unit.startswith(("minute", "минут")):
            return reference - timedelta(minutes=amount)
        if unit.startswith(("hour", "час")):
            return reference - timedelta(hours=amount)
        if unit.startswith(("week", "недел")):
            return reference - timedelta(weeks=amount)
        if unit.startswith(("month", "месяц")):
            return reference - timedelta(days=30 * amount)
        return reference - timedelta(days=amount)

    russian_date = re.search(
        r"(\d{1,2})\s+(" + "|".join(_RUSSIAN_MONTHS) + r")(?:\s+(\d{4}))?",
        text,
    )
    if russian_date:
        year = int(russian_date.group(3) or reference.year)
        return datetime(
            year,
            _RUSSIAN_MONTHS[russian_date.group(2)],
            int(russian_date.group(1)),
            tzinfo=UTC,
        )

    for date_format in ("%B %d, %Y", "%b %d, %Y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(value.strip(), date_format).replace(tzinfo=UTC)
        except ValueError:
            continue
    return None
