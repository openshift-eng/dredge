import argparse
import logging
import sys
from pathlib import Path
from urllib.parse import urlparse

import requests

from . import artifacts
from .discovery import JobFilter, from_github_pr, from_prow_history
from .fetcher import _auth
from .prow import Job

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    """Configure timestamped logging."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(
        description="Download artifacts from Prow CI jobs",
    )
    parser.add_argument(
        "--trusted-redirect-domain",
        action="append",
        default=[],
        help="Additional trusted domain for auth redirects "
        "(may be repeated; prefix with '.' for suffix match)",
    )

    discovery_parent = argparse.ArgumentParser(add_help=False)
    discovery_parent.add_argument(
        "-d",
        required=True,
        metavar="DIR",
        help="Output directory for downloaded artifacts",
    )
    discovery_parent.add_argument(
        "--auto-must-gather",
        action="store_true",
        help="Automatically download must-gather from steps that contain one",
    )
    discovery_parent.add_argument(
        "--auto-hypershift",
        action="store_true",
        help="Automatically download hypershift hosted cluster dumps",
    )
    discovery_parent.add_argument(
        "--auto",
        action="store_true",
        help="Enable all automatic artifact downloads",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    history_parser = subparsers.add_parser(
        "history",
        parents=[discovery_parent],
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
        parents=[discovery_parent],
        help="Download specific builds by their Spyglass URLs",
    )
    urls_parser.add_argument("urls", nargs="+", help="One or more Prow Spyglass URLs")
    urls_parser.set_defaults(func=cmd_urls)

    pr_parser = subparsers.add_parser(
        "pr",
        parents=[discovery_parent],
        help="Download failed prow jobs from a GitHub PR",
    )
    pr_parser.add_argument(
        "pr_url", help="GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)"
    )
    pr_parser.set_defaults(func=cmd_pr)

    mg_parser = subparsers.add_parser(
        "must-gather",
        help="Download must-gather from an existing build directory",
    )
    mg_parser.add_argument("build_dir", type=Path, help="Path to an existing build directory")
    mg_parser.add_argument(
        "step_name", nargs="?", default=None, help="Step name (guessed if omitted)"
    )
    mg_parser.set_defaults(func=cmd_must_gather)

    hs_parser = subparsers.add_parser(
        "hypershift-dump",
        help="Download hypershift hosted cluster dumps from an existing build directory",
    )
    hs_parser.add_argument("build_dir", type=Path, help="Path to an existing build directory")
    hs_parser.add_argument(
        "step_name", nargs="?", default=None, help="Step name (guessed if omitted)"
    )
    hs_parser.set_defaults(func=cmd_hypershift_dump)

    return parser.parse_args()


def _resolve_auto_flags(args: argparse.Namespace) -> tuple[bool, bool]:
    auto_must_gather = args.auto_must_gather or args.auto
    auto_hypershift = args.auto_hypershift or args.auto
    return auto_must_gather, auto_hypershift


def _load_job(build_dir: Path) -> Job:
    """Load Job from a build directory."""
    try:
        return Job(build_dir)
    except FileNotFoundError:
        logger.error(f"job.json or steps.json not found in {build_dir}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load job from {build_dir}: {e}")
        sys.exit(1)


def cmd_pr(args: argparse.Namespace, output_dir: Path | None) -> None:
    """Handle the 'pr' subcommand."""
    assert output_dir is not None
    logger.info(f"Fetching failed jobs for {args.pr_url}")

    try:
        urls = from_github_pr(args.pr_url, job_filter=JobFilter.FAILED)
    except ValueError as e:
        logger.error(str(e))
        sys.exit(1)
    except requests.RequestException as e:
        logger.error(f"Failed to fetch PR info from GitHub API: {e}")
        sys.exit(1)

    if not urls:
        logger.info("No failed prow jobs found for this PR")
        sys.exit(0)

    logger.info(f"Found {len(urls)} failed prow job(s)")
    for url in urls:
        logger.info(f"  {url}")

    auto_mg, auto_hs = _resolve_auto_flags(args)

    for i, url in enumerate(urls, 1):
        logger.info(f"--- Processing build {i}/{len(urls)} ---")
        artifacts.process_build(url, output_dir, auto_must_gather=auto_mg, auto_hypershift=auto_hs)

    try:
        artifacts.write_agents_md(output_dir)
    except OSError as e:
        logger.warning(f"Failed to write AGENTS.md: {e}")
    logger.info("Done")


def cmd_history(args: argparse.Namespace, output_dir: Path | None) -> None:
    """Handle the 'history' subcommand."""
    assert output_dir is not None
    parsed = urlparse(args.url)
    if not parsed.scheme or not parsed.netloc:
        logger.error("Invalid URL provided")
        sys.exit(1)

    if args.count < 1:
        logger.error("Count must be at least 1")
        sys.exit(1)

    if args.failure and args.success:
        job_filter = JobFilter.ALL
    elif args.failure:
        job_filter = JobFilter.FAILED
    elif args.success:
        job_filter = JobFilter.SUCCESS
    else:
        job_filter = JobFilter.FAILED

    filter_desc = []
    if args.failure:
        filter_desc.append("failures")
    if args.success:
        filter_desc.append("successes")
    filter_str = " (" + " or ".join(filter_desc) + ")" if filter_desc else ""

    logger.info(f"Starting download of {args.count} builds{filter_str} from: {args.url}")
    logger.info(f"Output directory: {output_dir.absolute()}")

    urls = from_prow_history(args.url, args.count, job_filter=job_filter)

    if not urls:
        logger.warning("No builds found matching criteria")
        sys.exit(0)

    logger.info(f"Processing {len(urls)} builds")

    auto_mg, auto_hs = _resolve_auto_flags(args)

    for i, url in enumerate(urls, 1):
        logger.info(f"--- Processing build {i}/{len(urls)} ---")
        artifacts.process_build(
            url, output_dir, auto_must_gather=auto_mg, auto_hypershift=auto_hs
        )

    try:
        artifacts.write_agents_md(output_dir)
    except OSError as e:
        logger.warning(f"Failed to write AGENTS.md: {e}")

    logger.info("Done")


def cmd_urls(args: argparse.Namespace, output_dir: Path | None) -> None:
    """Handle the 'urls' subcommand."""
    assert output_dir is not None
    logger.info(f"Downloading {len(args.urls)} builds by URL")
    logger.info(f"Output directory: {output_dir.absolute()}")

    auto_mg, auto_hs = _resolve_auto_flags(args)

    for i, url in enumerate(args.urls, 1):
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            logger.error(f"Invalid URL: {url}")
            continue

        build_id = url.rstrip("/").split("/")[-1]
        logger.info(f"--- Processing build {i}/{len(args.urls)} (ID: {build_id}) ---")
        artifacts.process_build(url, output_dir, auto_must_gather=auto_mg, auto_hypershift=auto_hs)

    try:
        artifacts.write_agents_md(output_dir)
    except OSError as e:
        logger.warning(f"Failed to write AGENTS.md: {e}")

    logger.info("Done")


def cmd_must_gather(args: argparse.Namespace, output_dir: Path | None) -> None:
    """Handle the 'must-gather' subcommand."""
    build_dir = Path(args.build_dir)
    if not build_dir.is_dir():
        logger.error(f"Build directory does not exist: {build_dir}")
        sys.exit(1)

    job = _load_job(build_dir)
    try:
        artifacts.download_must_gather(job, args.step_name)
    except artifacts.ArtifactError as e:
        logger.error(str(e))
        sys.exit(1)


def cmd_hypershift_dump(args: argparse.Namespace, output_dir: Path | None) -> None:
    """Handle the 'hypershift-dump' subcommand."""
    build_dir = Path(args.build_dir)
    if not build_dir.is_dir():
        logger.error(f"Build directory does not exist: {build_dir}")
        sys.exit(1)

    job = _load_job(build_dir)
    try:
        artifacts.download_hypershift_dumps(job, args.step_name)
    except artifacts.ArtifactError as e:
        logger.error(str(e))
        sys.exit(1)


def main() -> None:
    setup_logging()
    args = parse_args()

    _auth.configure(extra_trusted_domains=args.trusted_redirect_domain)

    output_dir = None
    if hasattr(args, "d"):
        output_dir = Path(args.d)
        output_dir.mkdir(parents=True, exist_ok=True)

    args.func(args, output_dir)
