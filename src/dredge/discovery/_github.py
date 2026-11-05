import json
import logging
import re
import subprocess
from collections.abc import Callable
from typing import Any

import requests

from ._types import GitHubDiscoveryError, GitHubTransport, JobFilter

logger = logging.getLogger(__name__)

JsonObject = dict[str, Any]
_GH_TIMEOUT = 30
_STATE_MAP = {
    JobFilter.FAILED: {"failure"},
    JobFilter.SUCCESS: {"success"},
    JobFilter.ALL: {"failure", "success"},
}


def from_github_pr(
    pr_url: str,
    job_filter: JobFilter,
    *,
    transport: GitHubTransport = GitHubTransport.HTTP,
) -> list[str]:
    match = re.match(r"https://github\.com/([^/]+)/([^/]+)/pull/(\d+)", pr_url)
    if not match:
        raise GitHubDiscoveryError(f"Invalid GitHub PR URL: {pr_url}")

    owner, repo, pr_number = match.group(1), match.group(2), match.group(3)
    read_json = _make_reader(transport)
    pr_endpoint = f"/repos/{owner}/{repo}/pulls/{pr_number}"
    pr = read_json(pr_endpoint)
    head = pr.get("head")
    head_sha = head.get("sha") if isinstance(head, dict) else None
    if not isinstance(head_sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", head_sha):
        raise GitHubDiscoveryError(f"Invalid GitHub response for {pr_endpoint}: expected head SHA")

    status_endpoint = f"/repos/{owner}/{repo}/commits/{head_sha}/status"
    statuses = read_json(status_endpoint).get("statuses", [])
    if not isinstance(statuses, list):
        raise GitHubDiscoveryError(
            f"Invalid GitHub response for {status_endpoint}: expected statuses list"
        )

    urls = []
    allowed_states = _STATE_MAP[job_filter]
    for status in statuses:
        if not isinstance(status, dict) or any(
            status.get(key) is not None and not isinstance(status[key], str)
            for key in ("state", "target_url")
        ):
            raise GitHubDiscoveryError(
                f"Invalid GitHub response for {status_endpoint}: expected status objects "
                "with string or null state and target_url"
            )
        target_url = status.get("target_url")
        if status.get("state") in allowed_states and target_url and "prow" in target_url:
            urls.append(target_url)
    return urls


def _make_reader(transport: GitHubTransport) -> Callable[[str], JsonObject]:
    if transport == GitHubTransport.GH:
        return _gh_get
    if transport != GitHubTransport.HTTP:
        raise ValueError("Unsupported GitHub transport")

    token = _get_github_token()
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    def read_json(endpoint: str) -> JsonObject:
        try:
            response = requests.get(
                f"https://api.github.com{endpoint}", headers=headers, timeout=30
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            code = exc.response.status_code if exc.response is not None else None
            detail = f"HTTP {code}" if code is not None else "network request failed"
            raise GitHubDiscoveryError(
                f"GitHub GET {endpoint}: {detail}. Check connectivity and repository access."
            ) from None
        try:
            payload = response.json()
        except ValueError:
            raise GitHubDiscoveryError(f"Invalid JSON from GitHub GET {endpoint}") from None
        return _json_object(payload, endpoint)

    return read_json


def _json_object(payload: object, endpoint: str) -> JsonObject:
    if not isinstance(payload, dict):
        raise GitHubDiscoveryError(f"Invalid GitHub response for {endpoint}: expected JSON object")
    return payload


def _gh_get(endpoint: str) -> JsonObject:
    try:
        result = subprocess.run(
            [
                "gh",
                "api",
                "--hostname",
                "github.com",
                "--method",
                "GET",
                "--include",
                "--header",
                "Accept: application/vnd.github+json",
                endpoint,
            ],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=_GH_TIMEOUT,
            check=False,
        )
    except FileNotFoundError:
        raise GitHubDiscoveryError(
            "GitHub gh transport requires the gh executable on PATH. Install GitHub CLI."
        ) from None
    except subprocess.TimeoutExpired:
        raise GitHubDiscoveryError(
            f"gh api GET {endpoint} timed out after {_GH_TIMEOUT}s. "
            "Check connectivity or reconnect the workspace before retrying."
        ) from None
    except (OSError, UnicodeError):
        raise GitHubDiscoveryError(
            f"Could not execute or decode gh api GET {endpoint}. Check the gh installation."
        ) from None

    # --include exposes request IDs without copying potentially sensitive stderr or bodies.
    framed = re.fullmatch(
        r"HTTP/\d(?:\.\d)? (\d{3})[^\n]*\n((?:[^\n]+\n)*)\n(.*)",
        result.stdout.replace("\r\n", "\n"),
        flags=re.DOTALL,
    )
    status = int(framed[1]) if framed else None
    request_ids = []
    if framed:
        for line in framed[2].splitlines():
            name, separator, value = line.partition(":")
            if separator and name.lower() in {"x-chai-request-id", "x-github-request-id"}:
                value = value.strip()
                if re.fullmatch(r"[A-Za-z0-9_:-]{1,128}", value):
                    request_ids.append(f"{name.lower()}={value}")
    context = f" ({', '.join(request_ids)})" if request_ids else ""
    if result.returncode or (status is not None and not 200 <= status < 300):
        detail = f"HTTP {status}, " if status is not None else ""
        raise GitHubDiscoveryError(
            f"gh api GET {endpoint} failed ({detail}exit {result.returncode}){context}. "
            "Check gh routing/authentication and repository access. "
            "For a stale workspace, reconnect before retrying."
        )
    if not framed:
        raise GitHubDiscoveryError(f"Invalid HTTP response framing from gh api GET {endpoint}")
    try:
        payload = json.loads(framed[3])
    except ValueError:
        raise GitHubDiscoveryError(f"Invalid JSON from gh api GET {endpoint}{context}") from None
    return _json_object(payload, endpoint)


def _get_github_token() -> str | None:
    try:
        return subprocess.check_output(
            ["gh", "auth", "token"], stderr=subprocess.DEVNULL, text=True
        ).strip()
    except (FileNotFoundError, subprocess.CalledProcessError):
        logger.warning("No GitHub token available, using unauthenticated requests")
        return None
