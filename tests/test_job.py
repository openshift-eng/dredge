import json

import pytest

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
            "src": {"success": True},
            "e2e-aws": {
                "success": False,
                "substeps": {
                    "setup": {"success": True},
                    "openshift-e2e-test": {"success": False},
                    "teardown": {"success": True},
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
        _write_job_files(job_dir, steps={
            "src": {"success": True},
            "e2e-aws": {
                "success": True,
                "substeps": {
                    "setup": {"success": True},
                    "openshift-e2e-test": {"success": True},
                },
            },
        })
        job = Job(job_dir)

        assert job.failed_steps() == []
