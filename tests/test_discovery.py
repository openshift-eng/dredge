import json
import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest
import requests
import responses

from dredge.discovery import (
    GitHubDiscoveryError,
    GitHubTransport,
    JobFilter,
    from_github_pr,
    from_prow_history,
)
from dredge.fetcher import FetchError

FIXTURES = Path(__file__).parent / "fixtures"

HISTORY_URL = (
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/"
    "pr-logs/directory/pull-ci-openshift-cluster-capi-operator-main-e2e-aws-capi-techpreview"
)
PROW_BASE = "https://prow.ci.openshift.org"


class TestFromProwHistory:
    @responses.activate
    def test_failed_filter_returns_only_failed_spyglass_urls(self):
        responses.get(HISTORY_URL, body=(FIXTURES / "prow_history_page1.html").read_bytes())

        urls = from_prow_history(HISTORY_URL, count=2, job_filter=JobFilter.FAILED)

        assert len(urls) == 2
        for url in urls:
            assert url.startswith(PROW_BASE + "/view/gs/")
        assert "2048460330580840448" in urls[0]
        assert "2048408136208879616" in urls[1]

    @responses.activate
    def test_success_filter_returns_only_successful_spyglass_urls(self):
        responses.get(HISTORY_URL, body=(FIXTURES / "prow_history_page1.html").read_bytes())

        urls = from_prow_history(HISTORY_URL, count=2, job_filter=JobFilter.SUCCESS)

        assert len(urls) == 2
        assert "2051228952428548096" in urls[0]
        assert "2049941819357138944" in urls[1]

    @responses.activate
    def test_all_filter_returns_both_success_and_failure(self):
        responses.get(HISTORY_URL, body=(FIXTURES / "prow_history_page1.html").read_bytes())

        urls = from_prow_history(HISTORY_URL, count=4, job_filter=JobFilter.ALL)

        assert len(urls) == 4

    @responses.activate
    def test_count_limits_returned_urls(self):
        responses.get(HISTORY_URL, body=(FIXTURES / "prow_history_page1.html").read_bytes())

        urls = from_prow_history(HISTORY_URL, count=1, job_filter=JobFilter.ALL)

        assert len(urls) == 1

    @responses.activate
    def test_paginates_via_older_runs_link(self):
        page2_url = (
            "https://prow.ci.openshift.org/job-history/gs/test-platform-results/"
            "pr-logs/directory/pull-ci-openshift-cluster-capi-operator-main-"
            "e2e-aws-capi-techpreview?buildId=2047281118289334272"
        )
        responses.get(HISTORY_URL, body=(FIXTURES / "prow_history_page1.html").read_bytes())
        responses.get(page2_url, body=(FIXTURES / "prow_history_page2.html").read_bytes())

        urls = from_prow_history(HISTORY_URL, count=3, job_filter=JobFilter.FAILED)

        assert len(urls) == 3
        assert len(responses.calls) == 2

    @responses.activate
    def test_returns_partial_results_when_history_exhausted(self):
        responses.get(HISTORY_URL, body=(FIXTURES / "prow_history_page2.html").read_bytes())

        urls = from_prow_history(HISTORY_URL, count=5, job_filter=JobFilter.FAILED)

        assert len(urls) == 1

    @responses.activate
    def test_network_error_raises(self):
        responses.get(HISTORY_URL, body=requests.ConnectionError("network down"))

        with pytest.raises(FetchError):
            from_prow_history(HISTORY_URL, count=1, job_filter=JobFilter.FAILED)


PR_URL = "https://github.com/openshift/cluster-capi-operator/pull/547"
HEAD_SHA = "a9f632b5299ee9512e35a610ba3fe495d64df6b8"
GH_API = "https://api.github.com"


class TestFromGithubPr:
    def _mock_github(self):
        import json

        responses.get(
            f"{GH_API}/repos/openshift/cluster-capi-operator/pulls/547",
            json=json.loads((FIXTURES / "github_pr.json").read_text()),
        )
        responses.get(
            f"{GH_API}/repos/openshift/cluster-capi-operator/commits/{HEAD_SHA}/status",
            json=json.loads((FIXTURES / "github_commit_status.json").read_text()),
        )

    @responses.activate
    def test_failed_filter_returns_only_failed_prow_urls(self):
        self._mock_github()

        urls = from_github_pr(PR_URL, job_filter=JobFilter.FAILED)

        assert len(urls) == 1
        assert "e2e-aws-capi-disconnected-techpreview" in urls[0]

    @responses.activate
    def test_success_filter_returns_only_successful_prow_urls(self):
        self._mock_github()

        urls = from_github_pr(PR_URL, job_filter=JobFilter.SUCCESS)

        assert len(urls) == 2
        assert "verify-deps" in urls[0]
        assert "vendor" in urls[1]

    @responses.activate
    def test_all_filter_returns_success_and_failure_but_not_pending(self):
        self._mock_github()

        urls = from_github_pr(PR_URL, job_filter=JobFilter.ALL)

        assert len(urls) == 3
        target_urls_str = " ".join(urls)
        assert "pending-job" not in target_urls_str

    def test_invalid_pr_url_raises(self):
        with pytest.raises(GitHubDiscoveryError, match="Invalid GitHub PR URL"):
            from_github_pr("https://example.com/not-a-pr", job_filter=JobFilter.FAILED)

    @responses.activate
    @pytest.mark.parametrize(
        "error", [FileNotFoundError("gh not found"), subprocess.CalledProcessError(1, "gh")]
    )
    def test_works_without_github_token(self, monkeypatch, error):
        original_check_output = subprocess.check_output

        def fake_check_output(cmd, **kwargs):
            if cmd == ["gh", "auth", "token"]:
                raise error
            return original_check_output(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "check_output", fake_check_output)
        self._mock_github()

        urls = from_github_pr(PR_URL, job_filter=JobFilter.FAILED)

        assert len(urls) == 1
        for call in responses.calls:
            assert "Authorization" not in call.request.headers

    @responses.activate
    def test_api_error_raises_without_anonymous_retry(self, monkeypatch):
        monkeypatch.setattr("dredge.discovery._github._get_github_token", lambda: "token")
        responses.get(
            f"{GH_API}/repos/openshift/cluster-capi-operator/pulls/547",
            status=403,
        )

        with pytest.raises(GitHubDiscoveryError, match="HTTP 403"):
            from_github_pr(PR_URL, job_filter=JobFilter.FAILED)
        assert len(responses.calls) == 1
        assert responses.calls[0].request.headers["Authorization"] == "Bearer token"


class TestPublicApi:
    def test_exports_are_restricted(self):
        import dredge.discovery as module

        assert set(module.__all__) == {
            "GitHubDiscoveryError",
            "GitHubTransport",
            "JobFilter",
            "from_github_pr",
            "from_prow_history",
        }


PR_ENDPOINT = "/repos/openshift/cluster-capi-operator/pulls/547"
STATUS_ENDPOINT = f"/repos/openshift/cluster-capi-operator/commits/{HEAD_SHA}/status"


def gh_response(payload, *, code=200, returncode=0, newline="\n", headers=None):
    if headers is None:
        headers = "Content-Type: application/json\nX-Chai-Request-Id: abc123\n"
    output = f"HTTP/2.0 {code} Status\n{headers}\n{json.dumps(payload)}"
    return subprocess.CompletedProcess(
        [], returncode, output.replace("\n", newline), "sensitive-stderr-token"
    )


@pytest.fixture
def gh_only(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("gh transport attempted token extraction or direct HTTP")

    monkeypatch.setattr("dredge.discovery._github._get_github_token", forbidden)
    monkeypatch.setattr("dredge.discovery._github.requests.get", forbidden)
    run = Mock()
    monkeypatch.setattr("dredge.discovery._github.subprocess.run", run)
    return run


@pytest.mark.parametrize("transport", list(GitHubTransport))
@pytest.mark.parametrize(
    ("job_filter", "indices"),
    [(JobFilter.FAILED, [2]), (JobFilter.SUCCESS, [0, 1]), (JobFilter.ALL, [0, 1, 2])],
)
@responses.activate
def test_transport_fixture_parity(monkeypatch, transport, job_filter, indices):
    pr = json.loads((FIXTURES / "github_pr.json").read_text())
    statuses = json.loads((FIXTURES / "github_commit_status.json").read_text())
    monkeypatch.setattr("dredge.discovery._github._get_github_token", lambda: "token")
    responses.get(f"{GH_API}{PR_ENDPOINT}", json=pr)
    responses.get(f"{GH_API}{STATUS_ENDPOINT}", json=statuses)
    run = Mock(side_effect=[gh_response(pr), gh_response(statuses)])
    monkeypatch.setattr("dredge.discovery._github.subprocess.run", run)

    assert from_github_pr(PR_URL, job_filter, transport=transport) == [
        statuses["statuses"][i]["target_url"] for i in indices
    ]
    if transport == GitHubTransport.HTTP:
        assert len(responses.calls) == 2
        assert all(c.request.headers["Authorization"] == "Bearer token" for c in responses.calls)
        run.assert_not_called()
    else:
        assert not responses.calls


@pytest.mark.parametrize("transport", list(GitHubTransport))
@responses.activate
def test_order_duplicates_case_sensitivity_and_base_repository(monkeypatch, transport):
    pr = {"head": {"sha": HEAD_SHA, "repo": {"full_name": "fork/other"}}}
    statuses = {
        "statuses": [
            {"state": "failure", "target_url": "https://prow.example/2"},
            {"state": "failure", "target_url": "https://PROW.example/1"},
            {"state": "failure", "target_url": "https://prow.example/2"},
            {"state": "error", "target_url": "https://prow.example/3"},
            {"state": "pending", "target_url": "https://prow.example/4"},
            {"state": "failure", "target_url": None},
            {},
        ]
    }
    monkeypatch.setattr("dredge.discovery._github._get_github_token", lambda: None)
    responses.get(f"{GH_API}{PR_ENDPOINT}", json=pr)
    responses.get(f"{GH_API}{STATUS_ENDPOINT}", json=statuses)
    run = Mock(side_effect=[gh_response(pr), gh_response(statuses)])
    monkeypatch.setattr("dredge.discovery._github.subprocess.run", run)
    assert from_github_pr(PR_URL, JobFilter.ALL, transport=transport) == [
        "https://prow.example/2",
        "https://prow.example/2",
    ]
    if transport == GitHubTransport.GH:
        assert [call.args[0][-1] for call in run.call_args_list] == [PR_ENDPOINT, STATUS_ENDPOINT]


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
@pytest.mark.parametrize("statuses", [{}, {"statuses": []}])
def test_gh_gets_use_explicit_host_method_closed_stdin_and_deadline(gh_only, newline, statuses):
    gh_only.side_effect = [
        gh_response({"head": {"sha": HEAD_SHA}}, newline=newline),
        gh_response(statuses, newline=newline, headers=""),
    ]
    assert from_github_pr(PR_URL, JobFilter.FAILED, transport=GitHubTransport.GH) == []
    for call, endpoint in zip(gh_only.call_args_list, [PR_ENDPOINT, STATUS_ENDPOINT], strict=True):
        assert call.args[0] == [
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
        ]
        assert call.kwargs["stdin"] == subprocess.DEVNULL
        assert call.kwargs["timeout"] == 30
        assert not call.kwargs.get("shell", False)
        assert "env" not in call.kwargs


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (FileNotFoundError("secret"), "Install GitHub CLI"),
        (PermissionError("secret"), "Check the gh installation"),
        (subprocess.TimeoutExpired("gh", 30, output="secret", stderr="secret"), "timed out"),
    ],
)
def test_gh_execution_errors_are_safe_and_never_fall_back(gh_only, failure, message):
    gh_only.side_effect = failure
    with pytest.raises(GitHubDiscoveryError, match=message) as exc:
        from_github_pr(PR_URL, JobFilter.FAILED, transport=GitHubTransport.GH)
    assert "secret" not in str(exc.value)
    assert gh_only.call_count == 1


@pytest.mark.parametrize("endpoint_index", [0, 1])
@pytest.mark.parametrize("code", [403, 404, 409, 503])
def test_gh_api_denial_and_service_errors_stop_discovery(gh_only, endpoint_index, code):
    replies = [gh_response({"head": {"sha": HEAD_SHA}})] * endpoint_index
    replies.append(gh_response({"message": "sensitive-body"}, code=code, returncode=1))
    gh_only.side_effect = replies
    with pytest.raises(GitHubDiscoveryError, match=f"HTTP {code}") as exc:
        from_github_pr(PR_URL, JobFilter.ALL, transport=GitHubTransport.GH)
    assert "x-chai-request-id=abc123" in str(exc.value)
    assert "sensitive" not in str(exc.value)
    assert (PR_ENDPOINT if endpoint_index == 0 else STATUS_ENDPOINT) in str(exc.value)
    assert gh_only.call_count == endpoint_index + 1


@pytest.mark.parametrize(
    ("reply", "message"),
    [
        (subprocess.CompletedProcess([], 1, "sensitive-body", "sensitive-stderr"), "exit 1"),
        (gh_response({}, code=403), "HTTP 403"),
        (subprocess.CompletedProcess([], 0, "sensitive-body", "secret"), "framing"),
        (subprocess.CompletedProcess([], 0, "HTTP/2.0 200 OK\n\nsensitive-body", ""), "JSON"),
        (gh_response([]), "JSON object"),
        (gh_response({}), "head SHA"),
        (gh_response({"head": {"sha": "sensitive/../status"}}), "head SHA"),
        (gh_response({"head": {"sha": 123}}), "head SHA"),
    ],
)
def test_gh_invalid_responses_do_not_become_empty_success(gh_only, reply, message):
    gh_only.return_value = reply
    with pytest.raises(GitHubDiscoveryError, match=message) as exc:
        from_github_pr(PR_URL, JobFilter.ALL, transport=GitHubTransport.GH)
    assert "sensitive" not in str(exc.value)
    assert gh_only.call_count == 1


@pytest.mark.parametrize("transport", list(GitHubTransport))
@pytest.mark.parametrize(
    "statuses",
    [
        None,
        {},
        [None],
        ["bad"],
        [{"state": []}],
        [
            {"state": "failure", "target_url": 1},
        ],
    ],
)
@responses.activate
def test_invalid_status_shapes_fail_for_both_transports(monkeypatch, transport, statuses):
    pr = {"head": {"sha": HEAD_SHA}}
    payload = {"statuses": statuses}
    monkeypatch.setattr("dredge.discovery._github._get_github_token", lambda: None)
    responses.get(f"{GH_API}{PR_ENDPOINT}", json=pr)
    responses.get(f"{GH_API}{STATUS_ENDPOINT}", json=payload)
    monkeypatch.setattr(
        "dredge.discovery._github.subprocess.run",
        Mock(
            side_effect=[
                gh_response(pr),
                gh_response(payload),
            ]
        ),
    )
    with pytest.raises(GitHubDiscoveryError, match="expected status"):
        from_github_pr(PR_URL, JobFilter.ALL, transport=transport)


@pytest.mark.parametrize("payload", [{}, {"statuses": []}])
@responses.activate
def test_http_empty_statuses_still_succeed(monkeypatch, payload):
    monkeypatch.setattr("dredge.discovery._github._get_github_token", lambda: None)
    responses.get(f"{GH_API}{PR_ENDPOINT}", json={"head": {"sha": HEAD_SHA}})
    responses.get(f"{GH_API}{STATUS_ENDPOINT}", json=payload)
    assert from_github_pr(PR_URL, JobFilter.FAILED) == []


@pytest.mark.parametrize("body", ["not-json", "[]", '{"head": null}'])
@responses.activate
def test_http_invalid_json_and_pr_shape_are_domain_errors(monkeypatch, body):
    monkeypatch.setattr("dredge.discovery._github._get_github_token", lambda: None)
    responses.get(f"{GH_API}{PR_ENDPOINT}", body=body)
    with pytest.raises(GitHubDiscoveryError):
        from_github_pr(PR_URL, JobFilter.FAILED)


@responses.activate
def test_http_network_failure_does_not_switch_to_gh(monkeypatch):
    monkeypatch.setattr("dredge.discovery._github._get_github_token", lambda: "token")
    run = Mock()
    monkeypatch.setattr("dredge.discovery._github.subprocess.run", run)
    responses.get(f"{GH_API}{PR_ENDPOINT}", body=requests.ConnectionError("sensitive"))
    with pytest.raises(GitHubDiscoveryError, match="network request failed") as exc:
        from_github_pr(PR_URL, JobFilter.FAILED)
    assert "sensitive" not in str(exc.value)
    assert len(responses.calls) == 1
    run.assert_not_called()
