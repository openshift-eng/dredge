from pathlib import Path

import pytest
import requests
import responses

from dredge.discovery import JobFilter, from_github_pr, from_prow_history
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
        with pytest.raises(ValueError, match="Invalid GitHub PR URL"):
            from_github_pr("https://example.com/not-a-pr", job_filter=JobFilter.FAILED)

    @responses.activate
    def test_works_without_gh_cli(self, monkeypatch):
        import subprocess

        original_check_output = subprocess.check_output

        def fake_check_output(cmd, **kwargs):
            if cmd == ["gh", "auth", "token"]:
                raise FileNotFoundError("gh not found")
            return original_check_output(cmd, **kwargs)

        monkeypatch.setattr(subprocess, "check_output", fake_check_output)
        self._mock_github()

        urls = from_github_pr(PR_URL, job_filter=JobFilter.FAILED)

        assert len(urls) == 1
        for call in responses.calls:
            assert "Authorization" not in call.request.headers

    @responses.activate
    def test_api_error_raises(self):
        responses.get(
            f"{GH_API}/repos/openshift/cluster-capi-operator/pulls/547",
            status=403,
        )

        with pytest.raises(requests.HTTPError):
            from_github_pr(PR_URL, job_filter=JobFilter.FAILED)


class TestPublicApi:
    def test_exports_are_restricted(self):
        import dredge.discovery as module

        assert set(module.__all__) == {"JobFilter", "from_github_pr", "from_prow_history"}
