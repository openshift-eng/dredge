#!/usr/bin/env python3
"""
Prow CI Job Artifact Downloader

Downloads artifacts from Prow CI jobs, including JUnit XML reports
and must-gather tarballs.
"""

import argparse
import json
import logging
import re
import sys
import tarfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

try:
    import requests
except ImportError:
    sys.exit("Error: 'requests' library required. Install with: pip install requests")

# Cache for discovered gcsweb base URL
_gcsweb_base_cache = {}


def parse_args():
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Download artifacts from Prow CI jobs",
    )
    parser.add_argument(
        "-o",
        "--output-dir",
        default=".",
        help="Output directory (default: current directory)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # history: download from job history page
    history_parser = subparsers.add_parser(
        "history",
        help="Download jobs from a job history page",
    )
    history_parser.add_argument("url", help="Prow job history page URL")
    history_parser.add_argument("count", type=int, help="Number of jobs to download")
    history_parser.add_argument(
        "--failure",
        action="store_true",
        help="Only download failed jobs",
    )
    history_parser.add_argument(
        "--success",
        action="store_true",
        help="Only download successful jobs",
    )
    history_parser.set_defaults(func=cmd_history)

    # urls: download specific builds by Spyglass URL
    urls_parser = subparsers.add_parser(
        "urls",
        help="Download specific builds by their Spyglass URLs",
    )
    urls_parser.add_argument("urls", nargs="+", help="One or more Prow Spyglass URLs")
    urls_parser.set_defaults(func=cmd_urls)

    return parser.parse_args()


def setup_logging():
    """Configure timestamped logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return logging.getLogger(__name__)


logger = setup_logging()


def fetch_page(url, retries=3, backoff=2):
    """HTTP GET with retries and exponential backoff."""
    for attempt in range(retries):
        try:
            logger.info(f"Fetching: {url}")
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            return response.text
        except requests.RequestException as e:
            if attempt < retries - 1:
                wait_time = backoff ** (attempt + 1)
                logger.warning(f"Request failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                logger.error(f"Request failed after {retries} attempts: {e}")
                raise
    return None


def extract_builds(html):
    """Extract allBuilds JSON from job history page HTML."""
    # Match: var allBuilds = [...];
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
        return builds  # No filter, return all

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
    # Link text is "&lt;- Older Runs" (HTML entity for "<- Older Runs")
    pattern = r'<a\s+href="([^"]+)"[^>]*>[^<]*Older\s+Runs[^<]*</a>'
    match = re.search(pattern, html, re.IGNORECASE)
    if match:
        relative_url = match.group(1)
        # Resolve relative URL against current URL
        next_url = urljoin(current_url, relative_url)
        logger.info(f"Found 'Older Runs' link: {next_url}")
        return next_url
    return None


def spyglass_to_gcs_path(spyglass_link):
    """Convert SpyglassLink to GCS path by stripping '/view/gs/' prefix."""
    prefix = "/view/gs/"
    if spyglass_link.startswith(prefix):
        return spyglass_link[len(prefix) :]
    # Handle alternate prefix
    prefix_alt = "/view/gcs/"
    if spyglass_link.startswith(prefix_alt):
        return spyglass_link[len(prefix_alt) :]
    logger.warning(f"Unexpected SpyglassLink format: {spyglass_link}")
    return spyglass_link


def discover_gcsweb_base(prow_base_url, spyglass_link):
    """
    Discover the gcsweb base URL by fetching the Spyglass page and extracting
    artifact links. Caches the result per prow instance.

    Returns the gcsweb base URL (e.g., "https://gcsweb.example.com/gcs/") or None.
    """
    # Check cache first
    if prow_base_url in _gcsweb_base_cache:
        return _gcsweb_base_cache[prow_base_url]

    spyglass_url = f"{prow_base_url}{spyglass_link}"
    try:
        logger.info(f"Discovering gcsweb URL from: {spyglass_url}")
        response = requests.get(spyglass_url, timeout=30)
        response.raise_for_status()
        html = response.text
    except requests.RequestException as e:
        logger.warning(f"Failed to fetch Spyglass page for gcsweb discovery: {e}")
        return None

    # Look for gcsweb links in the page
    # Pattern matches URLs like: https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/bucket/path
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
    path = parsed.path  # /view/gs/bucket/logs/job-name/BUILD_ID
    build_id = path.rstrip("/").split("/")[-1]
    return build_id, path


def download_artifact(url, dest, retries=3, backoff=2):
    """Stream download artifact to destination. Returns False on 404."""
    for attempt in range(retries):
        try:
            logger.info(f"Downloading: {url}")
            response = requests.get(url, stream=True, timeout=60)
            if response.status_code == 404:
                logger.info(f"Artifact not found (404): {url}")
                return False
            response.raise_for_status()

            dest.parent.mkdir(parents=True, exist_ok=True)
            with open(dest, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)
            logger.info(f"Downloaded to: {dest}")
            return True
        except requests.RequestException as e:
            if attempt < retries - 1:
                wait_time = backoff ** (attempt + 1)
                logger.warning(f"Download failed: {e}. Retrying in {wait_time}s...")
                time.sleep(wait_time)
            else:
                logger.warning(f"Download failed after {retries} attempts: {e}")
                return False
    return False


def discover_must_gather(gcs_path, gcsweb_base):
    """
    List artifacts directory to find must-gather location.
    Returns the full path to must-gather.tar if found, None otherwise.
    """
    artifacts_url = f"{gcsweb_base}{gcs_path}/artifacts/"

    try:
        logger.info(f"Listing artifacts directory: {artifacts_url}")
        response = requests.get(artifacts_url, timeout=30)
        if response.status_code == 404:
            logger.info("Artifacts directory not found")
            return None
        response.raise_for_status()
        html = response.text
    except requests.RequestException as e:
        logger.warning(f"Failed to list artifacts directory: {e}")
        return None

    # Parse directory listing for subdirectories (test step names)
    # gcsweb lists directories with trailing /
    subdir_pattern = r'<a\s+href="([^"]+)/"[^>]*>[^<]+/</a>'
    subdirs = re.findall(subdir_pattern, html)

    # Also try simpler pattern
    if not subdirs:
        subdir_pattern = r'href="([^"]+)/"'
        subdirs = re.findall(subdir_pattern, html)

    logger.info(f"Found {len(subdirs)} subdirectories in artifacts")

    # Check each subdirectory for must-gather
    for subdir in subdirs:
        # Clean up subdir name (may be relative or absolute path)
        subdir_name = subdir.rstrip("/").split("/")[-1]
        if not subdir_name or subdir_name == "..":
            continue

        must_gather_path = f"{subdir_name}/gather-must-gather/artifacts/must-gather.tar"
        full_path = f"{gcs_path}/artifacts/{must_gather_path}"

        # Quick HEAD check to see if file exists via gcsweb
        check_url = f"{gcsweb_base}{full_path}"
        try:
            head_resp = requests.head(check_url, timeout=10)
            if head_resp.status_code == 200:
                logger.info(f"Found must-gather at: {must_gather_path}")
                return full_path
        except requests.RequestException:
            continue

    logger.info("No must-gather found in any subdirectory")
    return None


def extract_tgz(tar_path, dest_dir):
    """Extract tar (gzip compressed) archive."""
    try:
        logger.info(f"Extracting: {tar_path} to {dest_dir}")
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Try gzip first, fall back to uncompressed
        try:
            with tarfile.open(tar_path, "r:gz") as tar:
                tar.extractall(path=dest_dir)
        except tarfile.ReadError:
            # Try uncompressed tar
            with tarfile.open(tar_path, "r:") as tar:
                tar.extractall(path=dest_dir)

        logger.info(f"Extracted successfully to: {dest_dir}")
        # Remove tar file after successful extraction
        tar_path.unlink()
        return True
    except (tarfile.TarError, OSError) as e:
        logger.warning(f"Extraction failed: {e}. Keeping tar file for inspection.")
        return False


def write_build_metadata(build, build_dir, prow_base_url):
    """Write metadata JSON file for a build."""
    refs = build.get("Refs", {})
    pulls = refs.get("pulls", [])
    spyglass_link = build.get("SpyglassLink", "")

    metadata = {
        "build_id": build.get("ID"),
        "execution_date": build.get("Started"),
        "prow_job_link": f"{prow_base_url}{spyglass_link}" if spyglass_link else None,
        "pr_link": pulls[0].get("link") if pulls else None,
        "commit_link": pulls[0].get("commit_link") if pulls else None,
    }

    metadata_path = build_dir / "build_info.json"
    with open(metadata_path, "w") as f:
        json.dump(metadata, f, indent=2)
    logger.info(f"Wrote metadata to: {metadata_path}")


def process_build(build, output_dir, prow_base_url):
    """Download artifacts for one build."""
    build_id = build.get("ID", "unknown")
    build_dir = output_dir / str(build_id)

    spyglass_link = build.get("SpyglassLink", "")

    if not spyglass_link:
        logger.warning(f"Build {build_id}: No SpyglassLink found")
        return

    logger.info(f"Processing build {build_id}")

    gcs_path = spyglass_to_gcs_path(spyglass_link)
    build_dir.mkdir(parents=True, exist_ok=True)

    # Discover gcsweb base URL from Spyglass page
    gcsweb_base = discover_gcsweb_base(prow_base_url, spyglass_link)
    if not gcsweb_base:
        logger.warning(f"Build {build_id}: Could not discover gcsweb URL, skipping artifact downloads")
        write_build_metadata(build, build_dir, prow_base_url)
        return

    # Download junit_operator.xml if not already present
    junit_dest = build_dir / "junit_operator.xml"
    if junit_dest.exists():
        logger.info(f"Build {build_id}: junit_operator.xml already exists, skipping")
    else:
        junit_url = f"{gcsweb_base}{gcs_path}/artifacts/junit_operator.xml"
        if not download_artifact(junit_url, junit_dest):
            logger.warning(f"Build {build_id}: junit_operator.xml not available")

    # Discover and download must-gather if not already present
    extract_dir = build_dir / "must-gather"
    if extract_dir.exists():
        logger.info(f"Build {build_id}: must-gather already exists, skipping")
    else:
        must_gather_gcs_path = discover_must_gather(gcs_path, gcsweb_base)
        if must_gather_gcs_path:
            must_gather_url = f"{gcsweb_base}{must_gather_gcs_path}"
            tar_dest = build_dir / "must-gather.tar"
            if download_artifact(must_gather_url, tar_dest):
                extract_tgz(tar_dest, extract_dir)

    # Always write build metadata
    write_build_metadata(build, build_dir, prow_base_url)

    logger.info(f"Completed processing build {build_id}")


def collect_builds(start_url, count, failure=False, success=False):
    """Paginate through job history to collect N builds matching filters."""
    builds_collected = []
    current_url = start_url

    while len(builds_collected) < count:
        try:
            html = fetch_page(current_url)
        except requests.RequestException:
            logger.error("Failed to fetch job history page")
            break

        builds = extract_builds(html)
        filtered = filter_builds(builds, failure=failure, success=success)
        builds_collected.extend(filtered)

        if len(builds_collected) >= count:
            break

        # Check for more pages
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


def cmd_history(args, output_dir):
    """Handle the 'history' subcommand."""
    # Validate URL
    parsed = urlparse(args.url)
    if not parsed.scheme or not parsed.netloc:
        logger.error("Invalid URL provided")
        sys.exit(1)

    if args.count < 1:
        logger.error("Count must be at least 1")
        sys.exit(1)

    # Extract prow base URL for gcsweb discovery
    prow_base_url = f"{parsed.scheme}://{parsed.netloc}"

    filter_desc = []
    if args.failure:
        filter_desc.append("failures")
    if args.success:
        filter_desc.append("successes")
    filter_str = " (" + " or ".join(filter_desc) + ")" if filter_desc else ""

    logger.info(f"Starting download of {args.count} builds{filter_str} from: {args.url}")
    logger.info(f"Output directory: {output_dir.absolute()}")

    # Collect builds across pages
    builds = collect_builds(args.url, args.count, failure=args.failure, success=args.success)

    if not builds:
        logger.warning("No builds found matching criteria")
        sys.exit(0)

    logger.info(f"Processing {len(builds)} builds")

    # Process each build
    for i, build in enumerate(builds, 1):
        logger.info(f"--- Processing build {i}/{len(builds)} ---")
        process_build(build, output_dir, prow_base_url)

    logger.info("Done")


def cmd_urls(args, output_dir):
    """Handle the 'urls' subcommand."""
    logger.info(f"Downloading {len(args.urls)} builds by URL")
    logger.info(f"Output directory: {output_dir.absolute()}")

    for i, url in enumerate(args.urls, 1):
        # Validate URL
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            logger.error(f"Invalid URL: {url}")
            continue

        # Extract prow base URL for gcsweb discovery
        prow_base_url = f"{parsed.scheme}://{parsed.netloc}"

        build_id, spyglass_path = parse_spyglass_url(url)
        logger.info(f"--- Processing build {i}/{len(args.urls)} (ID: {build_id}) ---")

        # Create minimal build dict for process_build
        build = {
            "ID": build_id,
            "SpyglassLink": spyglass_path,
        }

        process_build(build, output_dir, prow_base_url)

    logger.info("Done")


def main():
    args = parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    args.func(args, output_dir)


if __name__ == "__main__":
    main()
