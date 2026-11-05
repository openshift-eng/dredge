import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest

from dredge import commands
from dredge.discovery import GitHubDiscoveryError, GitHubTransport

PR_URL = "https://github.com/org/repo/pull/123"
SHA = "a" * 40


@pytest.fixture
def cli_environment(tmp_path):
    gh = tmp_path / "gh"
    gh.write_text(
        f"#!{sys.executable}\n"
        + """import json
import os
import sys
from pathlib import Path

with Path(os.environ["GH_CALL_LOG"]).open("a") as log:
    record = {"argv": sys.argv[1:], "routing": os.environ.get("GH_WORKSPACE_CONFIG")}
    log.write(json.dumps(record) + "\\n")
if sys.argv[1:2] != ["api"]:
    sys.exit("token extraction forbidden")
mode = os.environ.get("FIXTURE_MODE", "empty")
endpoint = sys.argv[-1]
if mode == "denied" or (mode == "status-denied" and endpoint.endswith("/status")):
    print("HTTP/2.0 403 Forbidden\\nX-Chai-Request-Id: fixture-id\\n\\n" +
          json.dumps({"message": "secret-payload"}))
    print("secret-stderr", file=sys.stderr)
    sys.exit(1)
if mode == "nonzero":
    print("secret-stderr", file=sys.stderr)
    sys.exit(7)
if mode == "malformed":
    print("HTTP/2.0 200 OK\\n\\nsecret-payload")
    sys.exit(0)
if mode == "shape":
    print("HTTP/2.0 200 OK\\n\\n[]")
    sys.exit(0)
payload = {"head": {"sha": "a" * 40}} if "/pulls/" in endpoint else {"statuses": []}
print("HTTP/2.0 200 OK\\nContent-Type: application/json\\n\\n" + json.dumps(payload))
"""
    )
    gh.chmod(0o755)
    # Make any accidental HTTP fallback observable without contacting the network.
    (tmp_path / "sitecustomize.py").write_text(
        "import requests\n"
        "def forbidden(*args, **kwargs):\n"
        "    raise AssertionError('DIRECT_HTTP_FORBIDDEN')\n"
        "requests.sessions.Session.request = forbidden\n"
    )
    log_path = tmp_path / "gh-calls.jsonl"
    return {
        **os.environ,
        "PATH": str(tmp_path),
        "PYTHONPATH": os.pathsep.join(
            [
                str(tmp_path),
                str(Path(__file__).resolve().parents[1] / "src"),
            ]
        ),
        "GH_CALL_LOG": str(log_path),
        "GH_WORKSPACE_CONFIG": "/fixture/credential-free-descriptor.json",
        "DREDGE_GITHUB_TRANSPORT": "gh",
    }, log_path


@pytest.mark.parametrize(
    ("mode", "exit_code", "message", "call_count"),
    [
        ("empty", 0, "No failed prow jobs", 2),
        ("denied", 1, "HTTP 403", 1),
        ("status-denied", 1, "HTTP 403", 2),
        ("nonzero", 1, "exit 7", 1),
        ("malformed", 1, "Invalid JSON", 1),
        ("shape", 1, "expected JSON object", 1),
    ],
)
def test_real_cli_with_fixture_gh(cli_environment, tmp_path, mode, exit_code, message, call_count):
    environment, log_path = cli_environment
    output_dir = tmp_path / "artifacts"
    result = subprocess.run(
        [sys.executable, "-m", "dredge", "-d", str(output_dir), "pr", PR_URL],
        capture_output=True,
        text=True,
        env={**environment, "FIXTURE_MODE": mode},
        timeout=10,
    )
    assert result.returncode == exit_code, result.stderr
    assert message in result.stderr
    assert "Traceback" not in result.stderr
    assert "secret" not in result.stderr
    assert "DIRECT_HTTP_FORBIDDEN" not in result.stderr
    assert list(output_dir.iterdir()) == []
    calls = [json.loads(line) for line in log_path.read_text().splitlines()]
    assert len(calls) == call_count
    for call, endpoint in zip(
        calls,
        [
            "/repos/org/repo/pulls/123",
            f"/repos/org/repo/commits/{SHA}/status",
        ],
        strict=False,
    ):
        assert call["argv"] == [
            "api",
            "--hostname",
            "github.com",
            "--method",
            "GET",
            "--include",
            "--header",
            "Accept: application/vnd.github+json",
            endpoint,
        ]
        assert call["routing"] == environment["GH_WORKSPACE_CONFIG"]


def test_real_cli_missing_gh_fails_cleanly(cli_environment, tmp_path):
    environment, _ = cli_environment
    (tmp_path / "gh").unlink()
    result = subprocess.run(
        [sys.executable, "-m", "dredge", "-d", str(tmp_path / "artifacts"), "pr", PR_URL],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
    )
    assert result.returncode == 1
    assert "Install GitHub CLI" in result.stderr
    assert "Traceback" not in result.stderr
    assert "DIRECT_HTTP_FORBIDDEN" not in result.stderr


@pytest.mark.parametrize("transport", list(GitHubTransport))
def test_cmd_pr_processes_the_shared_discovery_results(monkeypatch, tmp_path, transport):
    urls = ["https://prow.example/2", "https://prow.example/1", "https://prow.example/2"]
    discover = Mock(return_value=urls)
    process = Mock()
    monkeypatch.setattr(commands, "from_github_pr", discover)
    monkeypatch.setattr(commands, "_process_build", process)
    commands.cmd_pr(tmp_path, PR_URL, True, github_transport=transport)
    assert discover.call_args.kwargs["transport"] == transport
    assert [call.args for call in process.call_args_list] == [(url, tmp_path) for url in urls]
    assert all(call.kwargs == {"auto_must_gather": True} for call in process.call_args_list)


def test_cmd_pr_expected_error_stops_before_import(monkeypatch, tmp_path, caplog):
    process = Mock()
    monkeypatch.setattr(commands, "_process_build", process)
    monkeypatch.setattr(
        commands, "from_github_pr", Mock(side_effect=GitHubDiscoveryError("denied"))
    )
    with pytest.raises(SystemExit) as exc:
        commands.cmd_pr(tmp_path, PR_URL, False, github_transport=GitHubTransport.GH)
    assert exc.value.code == 1
    assert "denied" in caplog.text
    process.assert_not_called()


@pytest.mark.parametrize("error", [TypeError, ValueError, KeyError])
def test_cmd_pr_unexpected_programming_error_surfaces(monkeypatch, tmp_path, error):
    monkeypatch.setattr(commands, "from_github_pr", Mock(side_effect=error("bug")))
    with pytest.raises(error, match="bug"):
        commands.cmd_pr(tmp_path, PR_URL, False)


@pytest.mark.parametrize(
    ("url", "transport", "exit_code", "message"),
    [
        ("https://example.com/not-a-pr", "gh", 1, "Invalid GitHub PR URL"),
        (PR_URL, "unknown", 2, "DREDGE_GITHUB_TRANSPORT must be one of"),
    ],
)
def test_cli_invalid_inputs_fail_before_gh(
    cli_environment, tmp_path, url, transport, exit_code, message
):
    environment, log_path = cli_environment
    result = subprocess.run(
        [sys.executable, "-m", "dredge", "-d", str(tmp_path / "artifacts"), "pr", url],
        capture_output=True,
        text=True,
        env={**environment, "DREDGE_GITHUB_TRANSPORT": transport},
        timeout=10,
    )
    assert result.returncode == exit_code
    assert message in result.stderr
    assert "Traceback" not in result.stderr
    assert not log_path.exists()
