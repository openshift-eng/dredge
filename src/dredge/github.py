import logging
import subprocess

from . import http

logger = logging.getLogger(__name__)


class TokenError(Exception):
    pass


def get_github_token() -> str:
    """Get GitHub token from gh CLI. Raises TokenError if unavailable."""
    try:
        return subprocess.check_output(
            ["gh", "auth", "token"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except FileNotFoundError:
        raise TokenError("gh CLI not found; install GitHub CLI to authenticate")
    except subprocess.CalledProcessError:
        raise TokenError("gh CLI not logged in; run 'gh auth login'")


def fetch_failed_pr_jobs(owner: str, repo: str, pr_number: str, token: str | None = None) -> list[str]:
    """Fetch failed prow job URLs for a GitHub PR using the commit statuses API."""
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    pr_url = f"https://api.github.com/repos/{owner}/{repo}/pulls/{pr_number}"
    logger.info(f"Fetching PR info: {pr_url}")
    response = http.session_get(pr_url, headers=headers, timeout=30)
    response.raise_for_status()
    head_sha = response.json()["head"]["sha"]
    logger.info(f"PR head SHA: {head_sha}")

    status_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{head_sha}/status"
    logger.info(f"Fetching commit statuses: {status_url}")
    response = http.session_get(status_url, headers=headers, timeout=30)
    response.raise_for_status()
    statuses = response.json().get("statuses", [])

    failed_urls = [
        s["target_url"]
        for s in statuses
        if s.get("state") == "failure" and s.get("target_url") and "prow" in s["target_url"]
    ]

    return failed_urls
