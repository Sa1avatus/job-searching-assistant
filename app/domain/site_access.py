from dataclasses import dataclass
from urllib.parse import urlsplit, urlunsplit


class InvalidSiteAccess(ValueError):
    """Raised when site access configuration is unsafe or malformed."""


@dataclass(frozen=True, slots=True)
class ValidatedSiteAccess:
    login_url: str
    allowed_hosts: tuple[str, ...]


def validate_site_access(*, login_url: str, allowed_hosts: list[str]) -> ValidatedSiteAccess:
    """Validate and canonicalize an HTTPS login URL and exact host allowlist."""

    def invalid(message: str = "Invalid site access configuration") -> InvalidSiteAccess:
        return InvalidSiteAccess(message)

    def normalize_host(value: str) -> str:
        host = value.strip()
        if not host or len(host) > 253 or any(character.isspace() for character in host):
            raise invalid()
        if host.endswith("."):
            host = host[:-1]
        if not host or any(character in host for character in "/\\*:@?#[]"):
            raise invalid()

        source_labels = host.split(".")
        if any(not label for label in source_labels):
            raise invalid()
        try:
            labels = [label.encode("idna").decode("ascii").casefold() for label in source_labels]
        except (UnicodeError, ValueError):
            raise invalid() from None
        if any(
            not label or len(label) > 63 or label.startswith("-") or label.endswith("-")
            for label in labels
        ):
            raise invalid()

        normalized = ".".join(labels)
        if len(normalized) > 253:
            raise invalid()
        return normalized

    if type(login_url) is not str:
        raise invalid()
    stripped_url = login_url.strip()
    if not 1 <= len(stripped_url) <= 2000:
        raise invalid()
    if type(allowed_hosts) is not list or not 1 <= len(allowed_hosts) <= 50:
        raise invalid()
    if any(type(host) is not str for host in allowed_hosts):
        raise invalid()

    try:
        parsed = urlsplit(stripped_url)
        if parsed.scheme.casefold() != "https" or not parsed.hostname:
            raise invalid()
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise invalid()
        port = parsed.port
    except (UnicodeError, ValueError):
        raise invalid() from None
    if parsed.netloc.endswith(":"):
        raise invalid()

    normalized_hosts = tuple(sorted({normalize_host(host) for host in allowed_hosts}))
    normalized_login_host = normalize_host(parsed.hostname)
    if normalized_login_host not in normalized_hosts:
        raise InvalidSiteAccess("Login URL host is not allowed")

    netloc = normalized_login_host if port is None else f"{normalized_login_host}:{port}"
    canonical_url = urlunsplit(("https", netloc, parsed.path or "/", parsed.query, ""))
    return ValidatedSiteAccess(
        login_url=canonical_url,
        allowed_hosts=normalized_hosts,
    )
