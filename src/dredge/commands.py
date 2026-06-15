import logging
import sys
import tarfile
from pathlib import Path
from urllib.parse import urlparse

import requests

from .discovery import JobFilter, from_github_pr, from_prow_history
from .fetcher import FetchError
from .prow import Job, JobImportError, Step, import_from_spyglass

logger = logging.getLogger(__name__)


class ArtifactError(Exception):
    pass


def _download_failed_step_artifacts(job: Job) -> int:
    total = 0
    for step in job.failed_steps():
        try:
            step.get_log()
            total += 1
        except FetchError:
            pass

        try:
            for entry in step.list_artifacts("junit"):
                if entry.type == "file" and entry.filename.endswith(".xml"):
                    try:
                        step.get_artifact(f"junit/{entry.filename}")
                        total += 1
                    except FetchError:
                        pass
        except FetchError as e:
            logger.warning(f"Failed to list junit dir for {step.name}: {e}")

        try:
            for entry in step.list_artifacts():
                if (
                    entry.type == "file"
                    and entry.filename.startswith("junit")
                    and entry.filename.endswith(".xml")
                ):
                    try:
                        step.get_artifact(entry.filename)
                        total += 1
                    except FetchError:
                        pass
        except FetchError as e:
            logger.warning(f"Failed to list artifacts dir for {step.name}: {e}")

        logger.info(f"Downloaded artifacts for failed step {step.name}")

    return total


def _download_must_gather(job: Job, step_name: str | None = None) -> None:
    if step_name:
        step = job.step(step_name, "gather-must-gather")
    else:
        for top_step in job.steps():
            try:
                step = job.step(top_step.name, "gather-must-gather")
                break
            except KeyError:
                continue
        else:
            raise ArtifactError("No must-gather found in any test step")

    logger.info(f"Must-gather candidate: {step.step_path}/artifacts/must-gather.tar")
    try:
        step.extract_artifact("must-gather.tar")
    except (FetchError, tarfile.TarError, OSError) as e:
        raise ArtifactError(f"Failed to extract must-gather: {e}") from e


def _process_build(
    spyglass_url: str,
    output_dir: Path,
    auto_must_gather: bool = False,
) -> None:
    try:
        job = import_from_spyglass(spyglass_url, output_dir)
    except JobImportError as e:
        logger.warning(f"Failed to import job: {e}")
        return

    try:
        root_junits = job.get_root_junits()
        if root_junits:
            logger.info(
                f"Build {job.build_id}: downloaded {len(root_junits)} root junit(s)"
            )
    except FetchError as e:
        logger.warning(f"Build {job.build_id}: failed to fetch root junits: {e}")

    failed = job.failed_steps()
    if failed:
        count = _download_failed_step_artifacts(job)
        if count:
            logger.info(f"Build {job.build_id}: downloaded {count} artifact(s) for failed steps")
    else:
        logger.info(f"Build {job.build_id}: no failed steps found")

    if auto_must_gather:
        try:
            _download_must_gather(job)
        except ArtifactError as e:
            logger.warning(f"Build {job.build_id}: {e}")

    logger.info(f"Completed processing build {job.build_id}")


def _load_job(build_dir: Path) -> Job:
    try:
        return Job(build_dir)
    except FileNotFoundError:
        logger.error(f"job.json or steps.json not found in {build_dir}")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Failed to load job from {build_dir}: {e}")
        sys.exit(1)


def cmd_import(
    dredge_dir: Path,
    urls: list[str],
    auto_must_gather: bool,
) -> None:
    logger.info(f"Importing {len(urls)} builds by URL")
    logger.info(f"Output directory: {dredge_dir.absolute()}")

    for i, url in enumerate(urls, 1):
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            logger.error(f"Invalid URL: {url}")
            continue

        build_id = url.rstrip("/").split("/")[-1]
        logger.info(f"--- Processing build {i}/{len(urls)} (ID: {build_id}) ---")
        _process_build(url, dredge_dir, auto_must_gather=auto_must_gather)

    logger.info("Done")


def cmd_history(
    dredge_dir: Path,
    url: str,
    count: int,
    failed: bool,
    auto_must_gather: bool,
) -> None:
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        logger.error("Invalid URL provided")
        sys.exit(1)

    if count < 1:
        logger.error("Count must be at least 1")
        sys.exit(1)

    job_filter = JobFilter.FAILED if failed else JobFilter.ALL

    filter_str = " (failures only)" if failed else ""
    logger.info(f"Starting download of {count} builds{filter_str} from: {url}")
    logger.info(f"Output directory: {dredge_dir.absolute()}")

    discovered_urls = from_prow_history(url, count, job_filter=job_filter)

    if not discovered_urls:
        logger.warning("No builds found matching criteria")
        sys.exit(0)

    logger.info(f"Processing {len(discovered_urls)} builds")

    for i, build_url in enumerate(discovered_urls, 1):
        logger.info(f"--- Processing build {i}/{len(discovered_urls)} ---")
        _process_build(build_url, dredge_dir, auto_must_gather=auto_must_gather)

    logger.info("Done")


def cmd_pr(
    dredge_dir: Path,
    pr_url: str,
    auto_must_gather: bool,
) -> None:
    logger.info(f"Fetching failed jobs for {pr_url}")

    try:
        urls = from_github_pr(pr_url, job_filter=JobFilter.FAILED)
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

    for i, url in enumerate(urls, 1):
        logger.info(f"--- Processing build {i}/{len(urls)} ---")
        _process_build(url, dredge_dir, auto_must_gather=auto_must_gather)

    logger.info("Done")


def _resolve_step(job: Job, step_name: str) -> Step:
    parts = step_name.split("/", 1)
    if len(parts) == 2:
        return job.step(parts[0], parts[1])
    return job.step(parts[0])


def cmd_step_ls(dredge_dir: Path, build_id: str, step_name: str, path: str) -> None:
    import dataclasses
    import json

    job = _load_job(dredge_dir / build_id)
    step = _resolve_step(job, step_name)
    entries = step.list_artifacts(path)
    print(json.dumps([dataclasses.asdict(e) for e in entries], indent=2))


def cmd_step_log(dredge_dir: Path, build_id: str, step_name: str) -> None:
    job = _load_job(dredge_dir / build_id)
    step = _resolve_step(job, step_name)
    result = step.get_log()
    print(result)


def cmd_step_get(
    dredge_dir: Path, build_id: str, step_name: str, path: str, recursive: bool
) -> None:
    job = _load_job(dredge_dir / build_id)
    step = _resolve_step(job, step_name)
    result = step.get_artifact(path, recursive=recursive)
    print(result)


def cmd_step_extract(dredge_dir: Path, build_id: str, step_name: str, path: str) -> None:
    job = _load_job(dredge_dir / build_id)
    step = _resolve_step(job, step_name)
    result = step.extract_artifact(path)
    print(result)


def cmd_fetch_must_gather(
    dredge_dir: Path, build_id: str, step_name: str | None
) -> None:
    job = _load_job(dredge_dir / build_id)

    if step_name:
        step = _resolve_step(job, step_name)
    else:
        for top_step in job.steps():
            try:
                step = job.step(top_step.name, "gather-must-gather")
                break
            except KeyError:
                continue
        else:
            logger.error("No must-gather found in any test step")
            sys.exit(1)

    result = step.extract_artifact("must-gather.tar")
    print(result)




