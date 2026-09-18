"""Canonical vacancy identity: which posting on which source a URL points at.

Pure and deterministic. The same posting reached through different URL spellings
(tracking parameters, regional hosts, ``www``, trailing slashes, title slugs) must
produce the same ``(source_key, source_id)`` and the same ``canonical_url`` so that
re-importing or re-searching never creates a second vacancy row.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

SOURCE_HEADHUNTER = "headhunter"
SOURCE_LINKEDIN = "linkedin"
SOURCE_GREENHOUSE = "greenhouse"
SOURCE_REGISTRY = "registry"
SOURCE_OTHER = "other"
KNOWN_SOURCE_KEYS = frozenset(
    {SOURCE_HEADHUNTER, SOURCE_LINKEDIN, SOURCE_GREENHOUSE, SOURCE_REGISTRY, SOURCE_OTHER}
)

_ADAPTER_SOURCE_KEYS = {
    "greenhouse": SOURCE_GREENHOUSE,
    "google-registry": SOURCE_REGISTRY,
    "headhunter": SOURCE_HEADHUNTER,
    "hh": SOURCE_HEADHUNTER,
    "linkedin": SOURCE_LINKEDIN,
}

_TRACKING_PARAMS = frozenset(
    {
        "fbclid",
        "gclid",
        "yclid",
        "ref",
        "refid",
        "source",
        "from",
        "trk",
        "trackingid",
        "tracking",
        "query",
        "hhtmfrom",
        "hhtmfromlabel",
        "vacancy_id_from",
    }
)
_HH_ID = re.compile(r"/vacancy/(\d+)")
_LINKEDIN_VIEW_ID = re.compile(r"/jobs/view/(?:[^/?#]*-)?(\d{5,})")
_GREENHOUSE_PATH = re.compile(r"^/(?P<board>[^/]+)/jobs/(?P<job>\d+)")


@dataclass(frozen=True, slots=True)
class VacancyIdentity:
    source_key: str
    source_id: str | None
    canonical_url: str


def _host(netloc: str) -> str:
    host = netloc.rsplit("@", 1)[-1].split(":", 1)[0].casefold()
    return host.removeprefix("www.")


def _is_headhunter_host(host: str) -> bool:
    return host == "hh.ru" or host.endswith(".hh.ru") or host in {"hh.kz", "hh.uz", "hh.by"}


def _clean_query(query: str) -> str:
    kept = [
        (key, value)
        for key, value in parse_qsl(query, keep_blank_values=True)
        if key.casefold() not in _TRACKING_PARAMS and not key.casefold().startswith("utm_")
    ]
    return urlencode(sorted(kept))


def _generic_canonical(url: str) -> str:
    parts = urlsplit(url.strip())
    scheme = (parts.scheme or "https").casefold()
    if scheme == "http":
        scheme = "https"
    path = parts.path.rstrip("/") or ""
    return urlunsplit((scheme, _host(parts.netloc), path, _clean_query(parts.query), ""))


def canonicalize_vacancy_url(source_url: str, adapter_name: str = "generic") -> VacancyIdentity:
    """Return the canonical identity of a vacancy URL (deterministic, never raises)."""
    parts = urlsplit(source_url.strip())
    host = _host(parts.netloc)
    query = dict(parse_qsl(parts.query, keep_blank_values=True))

    if _is_headhunter_host(host):
        match = _HH_ID.search(parts.path)
        if match:
            vacancy_id = match.group(1)
            return VacancyIdentity(
                SOURCE_HEADHUNTER, vacancy_id, f"https://hh.ru/vacancy/{vacancy_id}"
            )
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        match = _LINKEDIN_VIEW_ID.search(parts.path)
        vacancy_id = match.group(1) if match else query.get("currentJobId")
        if vacancy_id and vacancy_id.isdigit():
            return VacancyIdentity(
                SOURCE_LINKEDIN, vacancy_id, f"https://www.linkedin.com/jobs/view/{vacancy_id}"
            )
    if host.endswith("greenhouse.io"):
        match = _GREENHOUSE_PATH.match(parts.path)
        if match:
            board, job = match.group("board"), match.group("job")
            return VacancyIdentity(
                SOURCE_GREENHOUSE,
                f"{board.casefold()}/{job}",
                f"https://boards.greenhouse.io/{board.casefold()}/jobs/{job}",
            )

    source_key = _ADAPTER_SOURCE_KEYS.get(adapter_name.casefold(), SOURCE_OTHER)
    return VacancyIdentity(source_key, None, _generic_canonical(source_url))


def vacancy_fingerprint(company: str, title: str, location: str = "") -> str:
    """Stable hash of normalised company/title/location for logical-duplicate *detection*.

    Not a merge key: two openings with the same title at one company are legitimately
    different postings, so this only groups candidates for review.
    """

    def normalise(value: str) -> str:
        return re.sub(r"[\W_]+", " ", value.casefold()).strip()

    payload = "|".join(normalise(part) for part in (company, title, location))
    return hashlib.sha1(payload.encode("utf-8"), usedforsecurity=False).hexdigest()
