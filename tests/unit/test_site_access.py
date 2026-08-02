import pytest

from app.domain.site_access import (
    InvalidSiteAccess,
    ValidatedSiteAccess,
    validate_site_access,
)


def test_validate_site_access_canonicalizes_valid_configuration() -> None:
    result = validate_site_access(
        login_url=" HTTPS://BÜCHER.example.:8443/login?next=%2Fprivate ",
        allowed_hosts=["other.example", "bücher.example", "BÜCHER.example."],
    )

    assert result == ValidatedSiteAccess(
        login_url="https://xn--bcher-kva.example:8443/login?next=%2Fprivate",
        allowed_hosts=("other.example", "xn--bcher-kva.example"),
    )


def test_validate_site_access_adds_root_path() -> None:
    result = validate_site_access(
        login_url="https://example.com",
        allowed_hosts=["EXAMPLE.COM"],
    )

    assert result.login_url == "https://example.com/"


@pytest.mark.parametrize(
    "login_url",
    [
        "http://example.com",
        "https://",
        "https://user@example.com",
        "https://user:secret@example.com",
        "https://example.com/path#fragment",
        "https://example.com:invalid",
        "https://example.com:65536",
        "https://example.com:",
        "https://[::1]/",
        "",
        " " * 2_001,
    ],
)
def test_validate_site_access_rejects_invalid_login_urls(login_url: str) -> None:
    with pytest.raises(InvalidSiteAccess):
        validate_site_access(login_url=login_url, allowed_hosts=["example.com"])


@pytest.mark.parametrize("login_url", [None, 123, b"https://example.com"])
def test_validate_site_access_rejects_non_string_login_url(login_url: object) -> None:
    with pytest.raises(InvalidSiteAccess):
        validate_site_access(  # type: ignore[arg-type]
            login_url=login_url,
            allowed_hosts=["example.com"],
        )


@pytest.mark.parametrize(
    "allowed_hosts",
    [
        None,
        "example.com",
        (),
        [],
        ["example.com"] * 51,
        [123],
    ],
)
def test_validate_site_access_rejects_invalid_allowlist_container(
    allowed_hosts: object,
) -> None:
    with pytest.raises(InvalidSiteAccess):
        validate_site_access(  # type: ignore[arg-type]
            login_url="https://example.com",
            allowed_hosts=allowed_hosts,
        )


@pytest.mark.parametrize(
    "host",
    [
        "",
        " ",
        "exa mple.com",
        "https://example.com",
        "example.com/path",
        "example.com\\path",
        "*.example.com",
        "user@example.com",
        "example.com:443",
        "example.com?query",
        "example.com#fragment",
        "[::1]",
        "example..com",
        "-example.com",
        "example-.com",
        f"{'a' * 64}.example",
        f"{'a' * 254}.com",
    ],
)
def test_validate_site_access_rejects_invalid_allowed_host(host: str) -> None:
    with pytest.raises(InvalidSiteAccess):
        validate_site_access(
            login_url="https://example.com",
            allowed_hosts=[host],
        )


@pytest.mark.parametrize(
    ("login_url", "allowed_hosts"),
    [
        ("https://sub.example.com", ["example.com"]),
        ("https://example.com", ["sub.example.com"]),
    ],
)
def test_validate_site_access_requires_exact_host_match(
    login_url: str,
    allowed_hosts: list[str],
) -> None:
    with pytest.raises(InvalidSiteAccess, match="^Login URL host is not allowed$"):
        validate_site_access(login_url=login_url, allowed_hosts=allowed_hosts)


def test_validate_site_access_does_not_reflect_sensitive_input() -> None:
    secret = "do-not-reflect"

    with pytest.raises(InvalidSiteAccess) as captured:
        validate_site_access(
            login_url=f"https://user:{secret}@example.com/?token={secret}",
            allowed_hosts=["example.com"],
        )

    assert secret not in str(captured.value)
