import json
import logging
import re
from urllib.parse import urljoin, urlparse

from ..fetcher import fetch_url
from ._types import JobFilter

logger = logging.getLogger(__name__)

_RESULT_MAP = {
    JobFilter.FAILED: {"FAILURE"},
    JobFilter.SUCCESS: {"SUCCESS"},
    JobFilter.ALL: {"FAILURE", "SUCCESS"},
}


def from_prow_history(url: str, count: int, job_filter: JobFilter) -> list[str]:
    parsed = urlparse(url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    collected: list[str] = []
    current_url = url

    while len(collected) < count:
        with fetch_url(current_url) as body:
            html = body.read().decode()

        builds = _extract_builds(html)
        filtered = _filter_builds(builds, job_filter)
        for build in filtered:
            collected.append(base_url + build["SpyglassLink"])

        if len(collected) >= count:
            break

        next_url = _get_next_page_url(html, current_url)
        if not next_url:
            break
        current_url = next_url

    return collected[:count]


def _extract_builds(html: str) -> list[dict]:
    match = re.search(r"var\s+allBuilds\s*=\s*(\[.*?\]);", html, re.DOTALL)
    if not match:
        raise ValueError("Could not find 'var allBuilds' in page HTML")
    builds: list[dict] = json.loads(match.group(1))
    return builds


def _filter_builds(builds: list[dict], job_filter: JobFilter) -> list[dict]:
    allowed = _RESULT_MAP[job_filter]
    return [b for b in builds if b.get("Result") in allowed]


def _get_next_page_url(html: str, current_url: str) -> str | None:
    match = re.search(
        r'<a\s+href="([^"]+)"[^>]*>[^<]*Older\s+Runs[^<]*</a>', html, re.IGNORECASE
    )
    if match:
        next_url: str = urljoin(current_url, match.group(1))
        return next_url
    return None
