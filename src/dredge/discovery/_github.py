import logging
import re
import subprocess

import requests

from ._types import JobFilter

logger = logging.getLogger(__name__)

_STATE_MAP = {
    JobFilter.FAILED: {"failure"},
    JobFilter.SUCCESS: {"success"},
    JobFilter.ALL: {"failure", "success"},
}


def from_github_pr(pr_url: str, job_filter: JobFilter) -> list[str]:
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not match:
        raise ValueError(f"Invalid GitHub PR URL: {pr_url}")

    owner, repo, pr_number = match.group(1), match.group(2), match.group(3)
    token = _get_github_token()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    pr_api_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    response = requests.get(pr_api_url, headers=headers, timeout=30)
    response.raise_for_status()
    head_sha = response.json()["head"]["sha"]

    status_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{head_sha}/status"
    response = requests.get(status_url, headers=headers, timeout=30)
    response.raise_for_status()
    statuses = response.json().get("statuses", [])

    allowed_states = _STATE_MAP[job_filter]
    return [
        s["target_url"]
        for s in statuses
        if s.get("state") in allowed_states
        and s.get("target_url")
        and "prow" in s["target_url"]
    ]


def _get_github_token() -> str | None:
    try:
        return subprocess.check_output(
            ["gh", "auth", "token"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        logger.warning("No GitHub token available, using unauthenticated requests")
        return None
