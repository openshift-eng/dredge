import argparse
import logging
import os
import sys
from pathlib import Path

from . import commands
from .fetcher import _auth
from .junit import filter_junit

logger = logging.getLogger(__name__)


def setup_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def parse_args() -> argparse.Namespace:
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
    parser.add_argument(
        "-d",
        metavar="DIR",
        default=os.environ.get("DREDGE_DIR"),
        help="Base directory for dredge data (env: DREDGE_DIR)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    import_parser = subparsers.add_parser(
        "import",
        help="Import specific builds by their Spyglass URLs",
    )
    import_parser.add_argument("urls", nargs="+", help="One or more Prow Spyglass URLs")
    import_parser.add_argument(
        "--auto-must-gather",
        action="store_true",
        help="Automatically download must-gather from steps that contain one",
    )

    history_parser = subparsers.add_parser(
        "history",
        help="Download jobs from a job history page",
    )
    history_parser.add_argument("url", help="Prow job history page URL")
    history_parser.add_argument("count", type=int, help="Number of jobs to download")
    history_parser.add_argument(
        "--failed",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Only download failed jobs (default: True)",
    )
    history_parser.add_argument(
        "--auto-must-gather",
        action="store_true",
        help="Automatically download must-gather from steps that contain one",
    )

    pr_parser = subparsers.add_parser(
        "pr",
        help="Download failed prow jobs from a GitHub PR",
    )
    pr_parser.add_argument(
        "pr_url", help="GitHub PR URL (e.g., https://github.com/owner/repo/pull/123)"
    )
    pr_parser.add_argument(
        "--auto-must-gather",
        action="store_true",
        help="Automatically download must-gather from steps that contain one",
    )

    step_ls_parser = subparsers.add_parser(
        "step-ls",
        help="List artifacts for a step",
    )
    step_ls_parser.add_argument("build_id", help="Build ID (directory name under DREDGE_DIR)")
    step_ls_parser.add_argument("step_name", help="Step path (e.g. e2e-aws/gather-must-gather)")
    step_ls_parser.add_argument(
        "-p", "--path", default="/", help="Artifact path to list (default: /)"
    )

    step_log_parser = subparsers.add_parser(
        "step-log",
        help="Download build log for a step",
    )
    step_log_parser.add_argument("build_id", help="Build ID")
    step_log_parser.add_argument("step_name", help="Step path")

    step_get_parser = subparsers.add_parser(
        "step-get",
        help="Download an artifact from a step",
    )
    step_get_parser.add_argument("build_id", help="Build ID")
    step_get_parser.add_argument("step_name", help="Step path")
    step_get_parser.add_argument(
        "-p", "--path", required=True, help="Artifact path to download"
    )
    step_get_parser.add_argument(
        "-r", "--recursive", action="store_true", help="Download directory recursively"
    )

    step_extract_parser = subparsers.add_parser(
        "step-extract",
        help="Extract a tar.gz artifact from a step",
    )
    step_extract_parser.add_argument("build_id", help="Build ID")
    step_extract_parser.add_argument("step_name", help="Step path")
    step_extract_parser.add_argument("path", help="Path to tar.gz artifact")

    fetch_mg_parser = subparsers.add_parser(
        "fetch-must-gather",
        help="Download and extract must-gather from a build",
    )
    fetch_mg_parser.add_argument("build_id", help="Build ID")
    fetch_mg_parser.add_argument(
        "-s", "--step-name", default=None, help="Step name (auto-detected if omitted)"
    )

    mg_parser = subparsers.add_parser(
        "must-gather",
        help="Download must-gather from an existing build directory",
    )
    mg_parser.add_argument("build_dir", type=Path, help="Path to an existing build directory")
    mg_parser.add_argument(
        "step_name", nargs="?", default=None, help="Step name (guessed if omitted)"
    )

    junit_filter_parser = subparsers.add_parser(
        "junit-filter",
        help="Filter JUnit XML by status, lifecycle, and flakiness",
    )
    junit_filter_parser.add_argument(
        "file",
        type=Path,
        help="JUnit XML file to filter (use - for stdin)",
    )
    junit_filter_parser.add_argument(
        "--status",
        choices=["failed", "passed", "skipped"],
        default=None,
        help="Keep only testcases with this status",
    )
    junit_filter_parser.add_argument(
        "--lifecycle",
        choices=["blocking", "informing"],
        default=None,
        help="Keep only testcases with this lifecycle (default: all)",
    )
    junit_filter_parser.add_argument(
        "--no-flaky",
        action="store_true",
        help="Exclude flaky tests (tests with both passing and failing entries)",
    )

    return parser.parse_args()


def _require_dredge_dir(args: argparse.Namespace) -> Path:
    if not args.d:
        logger.error("DREDGE_DIR is required: use -d or set the DREDGE_DIR environment variable")
        sys.exit(1)
    dredge_dir = Path(args.d)
    dredge_dir.mkdir(parents=True, exist_ok=True)
    return dredge_dir


def main() -> None:
    setup_logging()
    args = parse_args()

    _auth.configure(extra_trusted_domains=args.trusted_redirect_domain)

    if args.command == "import":
        dredge_dir = _require_dredge_dir(args)
        commands.cmd_import(dredge_dir, args.urls, args.auto_must_gather)

    elif args.command == "history":
        dredge_dir = _require_dredge_dir(args)
        commands.cmd_history(
            dredge_dir, args.url, args.count, args.failed, args.auto_must_gather
        )

    elif args.command == "pr":
        dredge_dir = _require_dredge_dir(args)
        commands.cmd_pr(dredge_dir, args.pr_url, args.auto_must_gather)

    elif args.command == "step-ls":
        dredge_dir = _require_dredge_dir(args)
        commands.cmd_step_ls(dredge_dir, args.build_id, args.step_name, args.path)

    elif args.command == "step-log":
        dredge_dir = _require_dredge_dir(args)
        commands.cmd_step_log(dredge_dir, args.build_id, args.step_name)

    elif args.command == "step-get":
        dredge_dir = _require_dredge_dir(args)
        commands.cmd_step_get(
            dredge_dir, args.build_id, args.step_name, args.path, args.recursive
        )

    elif args.command == "step-extract":
        dredge_dir = _require_dredge_dir(args)
        commands.cmd_step_extract(dredge_dir, args.build_id, args.step_name, args.path)

    elif args.command == "fetch-must-gather":
        dredge_dir = _require_dredge_dir(args)
        commands.cmd_fetch_must_gather(dredge_dir, args.build_id, args.step_name)

    elif args.command == "must-gather":
        commands.cmd_must_gather(args.build_dir, args.step_name)

    elif args.command == "junit-filter":
        xml_bytes = (
            sys.stdin.buffer.read() if str(args.file) == "-" else args.file.read_bytes()
        )
        result = filter_junit(
            xml_bytes,
            status=args.status,
            lifecycle=args.lifecycle,
            no_flaky=args.no_flaky,
        )
        sys.stdout.buffer.write(result)
