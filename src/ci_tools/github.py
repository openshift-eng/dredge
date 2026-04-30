import logging
import subprocess

import requests

logger = logging.getLogger(__name__)


def get_github_token():
    """Get GitHub token from gh CLI. Returns None if unavailable."""
    try:
        token = subprocess.check_output(
            ["gh", "auth", "token"], stderr=subprocess.DEVNULL, text=True
        ).strip()
        return token
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def fetch_failed_pr_jobs(owner, repo, pr_number, token=None):
    """Fetch failed prow job URLs for a GitHub PR using the commit statuses API."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    pr_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    logger.info(f"Fetching PR info: {pr_url}")
    response = requests.get(pr_url, headers=headers, timeout=30)
    response.raise_for_status()
    head_sha = response.json()["head"]["sha"]
    logger.info(f"PR head SHA: {head_sha}")

    status_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{head_sha}/status"
    logger.info(f"Fetching commit statuses: {status_url}")
    response = requests.get(status_url, headers=headers, timeout=30)
    response.raise_for_status()
    statuses = response.json().get("statuses", [])

    failed_urls = [
        s["target_url"]
        for s in statuses
        if s.get("state") == "failure" and s.get("target_url") and "prow" in s["target_url"]
    ]

    return failed_urls
