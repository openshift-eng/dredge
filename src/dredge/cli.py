import argparse
import logging
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import artifacts
from . import auth
from . import github
from . import prow

logger = logging.getLogger(__name__)


def setup_logging():
    """Configure timestamped logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


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
    parser.add_argument(
        "--trusted-redirect-domain",
        action="append",
        default=[],
        help="Additional trusted domain for auth redirects (may be repeated; prefix with '.' for suffix match)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

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

    urls_parser = subparsers.add_parser(
        "urls",
        help="Download specific builds by their Spyglass URLs",
    )
    urls_parser.add_argument("urls", nargs="+", help="One or more Prow Spyglass URLs")
    urls_parser.set_defaults(func=cmd_urls)

    pr_parser = subparsers.add_parser(
        "pr",
        help="Download failed prow jobs from a GitHub PR",
    )
    pr_parser.add_argument("pr_url", help="GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)")
    pr_parser.set_defaults(func=cmd_pr)

    return parser.parse_args()


def cmd_pr(args, output_dir):
    """Handle the 'pr' subcommand."""
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", args.pr_url)
    if not match:
        logger.error(f"Invalid GitHub PR URL: {args.pr_url}")
        sys.exit(1)

    owner, repo, pr_number = match.group(1), match.group(2), match.group(3)
    logger.info(f"Fetching failed jobs for {owner}/{repo}#{pr_number}")

    token = github.get_github_token()
    if not token:
        logger.warning("No GitHub token found (gh CLI not available or not logged in). "
                        "Using unauthenticated requests (rate-limited).")

    try:
        failed_urls = github.fetch_failed_pr_jobs(owner, repo, pr_number, token)
    except requests.RequestException as e:
        logger.error(f"Failed to fetch PR info from GitHub API: {e}")
        sys.exit(1)

    if not failed_urls:
        logger.info("No failed prow jobs found for this PR")
        sys.exit(0)

    logger.info(f"Found {len(failed_urls)} failed prow job(s)")
    for url in failed_urls:
        logger.info(f"  {url}")

    for i, url in enumerate(failed_urls, 1):
        parsed = urlparse(url)
        prow_base_url = f"{parsed.scheme}://{parsed.netloc}"
        build_id, spyglass_path = prow.parse_spyglass_url(url)
        logger.info(f"--- Processing build {i}/{len(failed_urls)} (ID: {build_id}) ---")

        build = {
            "ID": build_id,
            "SpyglassLink": spyglass_path,
        }
        artifacts.process_build(build, output_dir, prow_base_url)

    artifacts.write_agents_md(output_dir)
    logger.info("Done")


def cmd_history(args, output_dir):
    """Handle the 'history' subcommand."""
    parsed = urlparse(args.url)
    if not parsed.scheme or not parsed.netloc:
        logger.error("Invalid URL provided")
        sys.exit(1)

    if args.count < 1:
        logger.error("Count must be at least 1")
        sys.exit(1)

    prow_base_url = f"{parsed.scheme}://{parsed.netloc}"

    filter_desc = []
    if args.failure:
        filter_desc.append("failures")
    if args.success:
        filter_desc.append("successes")
    filter_str = " (" + " or ".join(filter_desc) + ")" if filter_desc else ""

    logger.info(f"Starting download of {args.count} builds{filter_str} from: {args.url}")
    logger.info(f"Output directory: {output_dir.absolute()}")

    builds = prow.collect_builds(args.url, args.count, failure=args.failure, success=args.success)

    if not builds:
        logger.warning("No builds found matching criteria")
        sys.exit(0)

    logger.info(f"Processing {len(builds)} builds")

    for i, build in enumerate(builds, 1):
        logger.info(f"--- Processing build {i}/{len(builds)} ---")
        artifacts.process_build(build, output_dir, prow_base_url)

    artifacts.write_agents_md(output_dir)

    logger.info("Done")


def cmd_urls(args, output_dir):
    """Handle the 'urls' subcommand."""
    logger.info(f"Downloading {len(args.urls)} builds by URL")
    logger.info(f"Output directory: {output_dir.absolute()}")

    for i, url in enumerate(args.urls, 1):
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            logger.error(f"Invalid URL: {url}")
            continue

        prow_base_url = f"{parsed.scheme}://{parsed.netloc}"

        build_id, spyglass_path = prow.parse_spyglass_url(url)
        logger.info(f"--- Processing build {i}/{len(args.urls)} (ID: {build_id}) ---")

        build = {
            "ID": build_id,
            "SpyglassLink": spyglass_path,
        }

        artifacts.process_build(build, output_dir, prow_base_url)

    artifacts.write_agents_md(output_dir)

    logger.info("Done")


def main():
    setup_logging()
    args = parse_args()

    auth.configure(extra_trusted_domains=args.trusted_redirect_domain)

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    args.func(args, output_dir)
