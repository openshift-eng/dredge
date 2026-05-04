import json
import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

from .fetch_url import fetch_url, FetchError

logger = logging.getLogger(__name__)


@dataclass
class Build:
    id: str
    spyglass_link: str
    started: str | None = None
    pr_link: str | None = None
    commit_link: str | None = None

    @classmethod
    def from_prow_json(cls, d: dict[str, Any]) -> "Build":
        refs = d.get("Refs") or {}
        pulls = refs.get("pulls", [])
        pull = pulls[0] if pulls else {}
        return cls(
            id=d.get("ID", "unknown"),
            spyglass_link=d.get("SpyglassLink", ""),
            started=d.get("Started"),
            pr_link=pull.get("link"),
            commit_link=pull.get("commit_link"),
        )


_gcsweb_base_cache = {}


def extract_builds(html: str) -> list[dict[str, Any]]:
    """Extract allBuilds JSON from job history page HTML."""
    pattern = r"var\s+allBuilds\s*=\s*(\[.*?\]);"
    match = re.search(pattern, html, re.DOTALL)
    if not match:
        raise ValueError("Could not find 'var allBuilds' in page HTML")

    try:
        builds = json.loads(match.group(1))
        logger.info(f"Found {len(builds)} builds on page")
        return builds
    except json.JSONDecodeError as e:
        raise ValueError(f"Failed to parse allBuilds JSON: {e}") from e


def filter_builds(builds: list[dict[str, Any]], failure: bool = False, success: bool = False) -> list[dict[str, Any]]:
    """
    Filter builds by result.

    - No flags: return all builds
    - --failure: return only FAILURE
    - --success: return only SUCCESS
    - Both flags: return FAILURE or SUCCESS (excludes PENDING, ABORTED, etc.)
    """
    if not failure and not success:
        return builds

    allowed = set()
    if failure:
        allowed.add("FAILURE")
    if success:
        allowed.add("SUCCESS")

    filtered = [b for b in builds if b.get("Result") in allowed]
    logger.info(f"Found {len(filtered)} builds matching filter (failure={failure}, success={success})")
    return filtered


def get_next_page_url(html: str, current_url: str) -> str | None:
    """Find 'Older Runs' pagination link."""
    pattern = r'<a\s+href="([^"]+)"[^>]*>[^<]*Older\s+Runs[^<]*</a>'
    match = re.search(pattern, html, re.IGNORECASE)
    if match:
        relative_url = match.group(1)
        next_url = urljoin(current_url, relative_url)
        logger.info(f"Found 'Older Runs' link: {next_url}")
        return next_url
    return None


def spyglass_to_gcs_path(spyglass_link: str) -> str:
    """Convert SpyglassLink to GCS path by stripping '/view/gs/' prefix."""
    prefix = "/view/gs/"
    if spyglass_link.startswith(prefix):
        return spyglass_link[len(prefix):]
    prefix_alt = "/view/gcs/"
    if spyglass_link.startswith(prefix_alt):
        return spyglass_link[len(prefix_alt):]
    logger.warning(f"Unexpected SpyglassLink format: {spyglass_link}")
    return spyglass_link


def discover_gcsweb_base(prow_base_url: str, spyglass_link: str) -> str:
    """
    Discover the gcsweb base URL by fetching the Spyglass page and extracting
    artifact links. Caches the result per prow instance.

    Raises FetchError on network errors.
    Raises ValueError when gcsweb pattern not found in HTML.
    """
    if prow_base_url in _gcsweb_base_cache:
        return _gcsweb_base_cache[prow_base_url]

    spyglass_url = f"{prow_base_url}{spyglass_link}"
    logger.info(f"Discovering gcsweb URL from: {spyglass_url}")
    with fetch_url(spyglass_url) as body:
        html = body.read().decode()

    gcsweb_pattern = r'(https?://[^"\s]+/gcs/)[^"\s]+'
    match = re.search(gcsweb_pattern, html)

    if not match:
        raise ValueError("Could not discover gcsweb URL from Spyglass page")

    gcsweb_base = match.group(1)
    logger.info(f"Discovered gcsweb base URL: {gcsweb_base}")
    _gcsweb_base_cache[prow_base_url] = gcsweb_base
    return gcsweb_base


def parse_spyglass_url(url: str) -> tuple[str, str]:
    """
    Parse a Spyglass URL into build ID and SpyglassLink path.

    Input:  https://prow.ci.openshift.org/view/gs/bucket/path/BUILD_ID
    Output: (build_id, "/view/gs/bucket/path/BUILD_ID")
    """
    parsed = urlparse(url)
    path = parsed.path
    build_id = path.rstrip("/").split("/")[-1]
    return build_id, path


def collect_builds(start_url: str, count: int, failure: bool = False, success: bool = False) -> list[dict[str, Any]]:
    """Paginate through job history to collect N builds matching filters."""
    builds_collected = []
    current_url = start_url

    while len(builds_collected) < count:
        try:
            with fetch_url(current_url) as body:
                html = body.read().decode()
        except FetchError:
            logger.error("Failed to fetch job history page")
            break

        try:
            builds = extract_builds(html)
        except ValueError as e:
            logger.error(f"Failed to parse job history page: {e}")
            break
        filtered = filter_builds(builds, failure=failure, success=success)
        builds_collected.extend(filtered)

        if len(builds_collected) >= count:
            break

        next_url = get_next_page_url(html, current_url)
        if not next_url:
            logger.info("No more pages available")
            break

        current_url = next_url

    if len(builds_collected) < count:
        logger.warning(
            f"Requested {count} builds but only found {len(builds_collected)} available"
        )

    return builds_collected[:count]
