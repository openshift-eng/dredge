import json
import logging
import re
import sys
from urllib.parse import urljoin, urlparse

import requests

from . import http

logger = logging.getLogger(__name__)

_gcsweb_base_cache = {}


def extract_builds(html):
    """Extract allBuilds JSON from job history page HTML."""
    pattern = r"var\s+allBuilds\s*=\s*(\[.*?\]);"
    match = re.search(pattern, html, re.DOTALL)
    if not match:
        logger.error("Could not find 'var allBuilds' in page HTML")
        sys.exit(1)

    try:
        builds = json.loads(match.group(1))
        logger.info(f"Found {len(builds)} builds on page")
        return builds
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse allBuilds JSON: {e}")
        sys.exit(1)


def filter_builds(builds, failure=False, success=False):
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


def get_next_page_url(html, current_url):
    """Find 'Older Runs' pagination link."""
    pattern = r'<a\s+href="([^"]+)"[^>]*>[^<]*Older\s+Runs[^<]*</a>'
    match = re.search(pattern, html, re.IGNORECASE)
    if match:
        relative_url = match.group(1)
        next_url = urljoin(current_url, relative_url)
        logger.info(f"Found 'Older Runs' link: {next_url}")
        return next_url
    return None


def spyglass_to_gcs_path(spyglass_link):
    """Convert SpyglassLink to GCS path by stripping '/view/gs/' prefix."""
    prefix = "/view/gs/"
    if spyglass_link.startswith(prefix):
        return spyglass_link[len(prefix):]
    prefix_alt = "/view/gcs/"
    if spyglass_link.startswith(prefix_alt):
        return spyglass_link[len(prefix_alt):]
    logger.warning(f"Unexpected SpyglassLink format: {spyglass_link}")
    return spyglass_link


def discover_gcsweb_base(prow_base_url, spyglass_link):
    """
    Discover the gcsweb base URL by fetching the Spyglass page and extracting
    artifact links. Caches the result per prow instance.

    Returns the gcsweb base URL (e.g., "https://gcsweb.example.com/gcs/") or None.
    """
    if prow_base_url in _gcsweb_base_cache:
        return _gcsweb_base_cache[prow_base_url]

    spyglass_url = f"{prow_base_url}{spyglass_link}"
    try:
        logger.info(f"Discovering gcsweb URL from: {spyglass_url}")
        response = http.session_get(spyglass_url, timeout=30)
        response.raise_for_status()
        html = response.text
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch Spyglass page for gcsweb discovery: {e}")
        return None

    gcsweb_pattern = r'(https?://[^"\s]+/gcs/)[^"\s]+'
    match = re.search(gcsweb_pattern, html)

    if match:
        gcsweb_base = match.group(1)
        logger.info(f"Discovered gcsweb base URL: {gcsweb_base}")
        _gcsweb_base_cache[prow_base_url] = gcsweb_base
        return gcsweb_base

    logger.warning("Could not discover gcsweb URL from Spyglass page")
    return None


def parse_spyglass_url(url):
    """
    Parse a Spyglass URL into build ID and SpyglassLink path.

    Input:  https://prow.ci.openshift.org/view/gs/bucket/path/BUILD_ID
    Output: (build_id, "/view/gs/bucket/path/BUILD_ID")
    """
    parsed = urlparse(url)
    path = parsed.path
    build_id = path.rstrip("/").split("/")[-1]
    return build_id, path


def collect_builds(start_url, count, failure=False, success=False):
    """Paginate through job history to collect N builds matching filters."""
    builds_collected = []
    current_url = start_url

    while len(builds_collected) < count:
        try:
            html = http.fetch_page(current_url)
        except requests.RequestException:
            logger.error("Failed to fetch job history page")
            break

        builds = extract_builds(html)
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
