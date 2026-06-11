import json

import pytest
import responses

from dredge.prow import Job, Step

GCS_PATH = "test-bucket/pr-logs/pull/org_repo/123/job-name/9999"
GCSWEB_BASE = "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/"


def _write_job_files(job_dir, steps=None):
    job_dir.mkdir(parents=True, exist_ok=True)
    job_data = {
        "spyglass": "https://prow.example.com/view/gs/bucket/9999",
        "build_id": "9999",
        "job_name": "pull-ci-org-repo-main-e2e",
        "job_type": "presubmit",
        "pr_link": "https://github.com/org/repo/pull/123",
        "gcs_path": GCS_PATH,
        "gcsweb_base": GCSWEB_BASE,
    }
    (job_dir / "job.json").write_text(json.dumps(job_data))

    if steps is None:
        steps = {
            "src": {"status": "passed", "type": "build"},
            "e2e-aws": {
                "status": "failed",
                "type": "test",
                "substeps": {
                    "setup": {"status": "passed"},
                    "openshift-e2e-test": {"status": "failed"},
                    "teardown": {"status": "passed"},
                },
            },
        }
    (job_dir / "steps.json").write_text(json.dumps(steps))


class TestJobLoadsMetadata:
    def test_all_attributes(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)

        job = Job(job_dir)

        assert job.job_dir == job_dir
        assert job.spyglass == "https://prow.example.com/view/gs/bucket/9999"
        assert job.build_id == "9999"
        assert job.job_name == "pull-ci-org-repo-main-e2e"
        assert job.job_type == "presubmit"
        assert job.pr_link == "https://github.com/org/repo/pull/123"
        assert job.gcs_path == GCS_PATH
        assert job.gcsweb_base == GCSWEB_BASE

    def test_missing_files(self, tmp_path):
        job_dir = tmp_path / "missing"
        job_dir.mkdir()

        with pytest.raises(FileNotFoundError):
            Job(job_dir)


class TestJobStep:
    def test_top_level(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        step = job.step("src")

        assert isinstance(step, Step)
        assert step.name == "src"
        assert step.success is True
        assert step.test_name is None
        assert step.step_path == "src"

    def test_inner(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        step = job.step("e2e-aws", "openshift-e2e-test")

        assert isinstance(step, Step)
        assert step.name == "openshift-e2e-test"
        assert step.success is False
        assert step.test_name == "e2e-aws"
        assert step.step_path == "e2e-aws/openshift-e2e-test"

    def test_step_type_propagated(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        assert job.step("src").step_type == "build"
        assert job.step("e2e-aws").step_type == "test"

    def test_unknown_raises_key_error(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        with pytest.raises(KeyError, match="no-such-step"):
            job.step("no-such-step")

    def test_unknown_inner_raises_key_error(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        with pytest.raises(KeyError, match="e2e-aws/no-such-inner"):
            job.step("e2e-aws", "no-such-inner")


class TestJobSteps:
    def test_returns_all_top_level(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        steps = job.steps()

        assert len(steps) == 2
        names = {s.name for s in steps}
        assert names == {"src", "e2e-aws"}
        for s in steps:
            assert isinstance(s, Step)
            assert s.test_name is None


class TestGetRootJunits:
    @responses.activate
    def test_downloads_from_both_prefixes(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        root_url = f"{GCSWEB_BASE}{GCS_PATH}/"
        root_gcs_prefix = f"/gcs/{GCS_PATH}"
        root_html = (
            f'<html><ul>'
            f'<li><a href="{root_gcs_prefix}/prowjob_junit.xml">prowjob_junit.xml</a></li>'
            f'<li><a href="{root_gcs_prefix}/artifacts/">artifacts/</a></li>'
            f'</ul></html>'
        )
        responses.get(root_url, body=root_html.encode())

        artifacts_url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/"
        art_gcs_prefix = f"/gcs/{GCS_PATH}/artifacts"
        art_html = (
            f'<html><ul>'
            f'<li><a href="{art_gcs_prefix}/junit_operator.xml">'
            f'junit_operator.xml</a></li>'
            f'<li><a href="{art_gcs_prefix}/ci-operator-step-graph.json">'
            f'ci-operator-step-graph.json</a></li>'
            f'</ul></html>'
        )
        responses.get(artifacts_url, body=art_html.encode())

        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/prowjob_junit.xml",
            body=b"<xml>prowjob</xml>",
        )
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/junit_operator.xml",
            body=b"<xml>operator</xml>",
        )

        result = job.get_root_junits()

        assert len(result) == 2
        filenames = {p.name for p in result}
        assert filenames == {"prowjob_junit.xml", "junit_operator.xml"}
        assert (job_dir / "prowjob_junit.xml").read_text() == "<xml>prowjob</xml>"
        assert (job_dir / "junit_operator.xml").read_text() == "<xml>operator</xml>"

    @responses.activate
    def test_skips_non_junit_xml(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        root_url = f"{GCSWEB_BASE}{GCS_PATH}/"
        root_gcs_prefix = f"/gcs/{GCS_PATH}"
        root_html = (
            f'<html><ul>'
            f'<li><a href="{root_gcs_prefix}/prowjob.json">prowjob.json</a></li>'
            f'<li><a href="{root_gcs_prefix}/started.json">started.json</a></li>'
            f'</ul></html>'
        )
        responses.get(root_url, body=root_html.encode())

        artifacts_url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/"
        art_gcs_prefix = f"/gcs/{GCS_PATH}/artifacts"
        art_html = (
            f'<html><ul>'
            f'<li><a href="{art_gcs_prefix}/ci-operator-step-graph.json">'
            f'ci-operator-step-graph.json</a></li>'
            f'</ul></html>'
        )
        responses.get(artifacts_url, body=art_html.encode())

        result = job.get_root_junits()

        assert result == []

    @responses.activate
    def test_idempotent(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        root_url = f"{GCSWEB_BASE}{GCS_PATH}/"
        root_gcs_prefix = f"/gcs/{GCS_PATH}"
        root_html = (
            f'<html><ul>'
            f'<li><a href="{root_gcs_prefix}/prowjob_junit.xml">prowjob_junit.xml</a></li>'
            f'</ul></html>'
        )
        artifacts_url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/"
        art_html = '<html><ul></ul></html>'

        responses.get(root_url, body=root_html.encode())
        responses.get(artifacts_url, body=art_html.encode())
        responses.get(f"{GCSWEB_BASE}{GCS_PATH}/prowjob_junit.xml", body=b"<xml/>")

        def _download_calls():
            return [
                c for c in responses.calls
                if "prowjob_junit.xml" in c.request.url
                and not c.request.url.endswith("/")
            ]

        job.get_root_junits()
        assert len(_download_calls()) == 1

        # Second call — re-lists but does not re-download
        responses.get(root_url, body=root_html.encode())
        responses.get(artifacts_url, body=art_html.encode())
        job.get_root_junits()
        assert len(_download_calls()) == 1


class TestFailedSteps:
    def test_returns_failed_inner_steps(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(job_dir)
        job = Job(job_dir)

        failed = job.failed_steps()

        assert len(failed) == 1
        assert failed[0].name == "openshift-e2e-test"
        assert failed[0].test_name == "e2e-aws"
        assert failed[0].success is False

    def test_empty_when_all_pass(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(
            job_dir,
            steps={
                "src": {"status": "passed", "type": "build"},
                "e2e-aws": {
                    "status": "passed",
                    "type": "test",
                    "substeps": {
                        "setup": {"status": "passed"},
                        "openshift-e2e-test": {"status": "passed"},
                    },
                },
            },
        )
        job = Job(job_dir)

        assert job.failed_steps() == []

    def test_returns_failed_top_level_without_substeps(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(
            job_dir,
            steps={
                "test": {"status": "failed", "type": "test"},
            },
        )
        job = Job(job_dir)

        failed = job.failed_steps()

        assert len(failed) == 1
        assert failed[0].name == "test"
        assert failed[0].test_name is None
        assert failed[0].success is False
        assert failed[0].step_path == "test"

    def test_returns_failed_build_steps(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(
            job_dir,
            steps={
                "src": {"status": "passed", "type": "build"},
                "azure-cloud-controller-manager": {"status": "failed", "type": "build"},
                "e2e-test": {"status": "skipped", "type": "test"},
            },
        )
        job = Job(job_dir)

        failed = job.failed_steps()

        assert len(failed) == 1
        assert failed[0].name == "azure-cloud-controller-manager"
        assert failed[0].step_type == "build"
        assert failed[0].success is False

    def test_skipped_steps_not_in_failed(self, tmp_path):
        job_dir = tmp_path / "9999"
        _write_job_files(
            job_dir,
            steps={
                "src": {"status": "passed", "type": "build"},
                "e2e-test": {"status": "skipped", "type": "test"},
            },
        )
        job = Job(job_dir)

        assert job.failed_steps() == []
