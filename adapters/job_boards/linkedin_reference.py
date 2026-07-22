from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import urlparse


@dataclass(frozen=True, slots=True)
class LinkedInJobReference:
    job_id: str
    source_url: str
    extraction_mode: str = "manual"
    submission_supported: bool = False
    limitation: str = (
        "LinkedIn job search and application APIs require approved partner access; "
        "scraping and unauthorized browser automation are not supported"
    )

    @classmethod
    def from_url(cls, url: str) -> LinkedInJobReference:
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.hostname != "www.linkedin.com":
            raise ValueError("URL is not a supported LinkedIn job URL")
        if parsed.username is not None or parsed.password is not None:
            raise ValueError("LinkedIn URL must not contain credentials")
        try:
            port = parsed.port
        except ValueError as error:
            raise ValueError("LinkedIn URL port is invalid") from error
        if port not in {None, 443}:
            raise ValueError("LinkedIn URL must use the standard HTTPS port")
        match = re.fullmatch(r"/jobs/view/(?:[^/]*-)?(\d+)/?", parsed.path)
        if match is None:
            raise ValueError("LinkedIn URL must contain /jobs/view/{job_id}")
        job_id = match.group(1)
        return cls(job_id, f"https://www.linkedin.com/jobs/view/{job_id}")
