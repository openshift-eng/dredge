import json
from pathlib import Path

import pytest
import responses

from dredge.import_job import import_job, JobImportError

FIXTURES = Path(__file__).parent / "fixtures"

SPYGLASS_URL = (
    "https://prow.ci.openshift.org/view/gs/"
    "test-platform-results/pr-logs/pull/openshift_ci-tools/5151/"
    "pull-ci-openshift-ci-tools-main-breaking-changes/2050218808576053248"
)
BUILD_ID = "2050218808576053248"
GCS_PATH = (
    "test-platform-results/pr-logs/pull/openshift_ci-tools/5151/"
    "pull-ci-openshift-ci-tools-main-breaking-changes/2050218808576053248"
)
GCSWEB_BASE = "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/"


def _mock_all():
    responses.get(SPYGLASS_URL, body=(FIXTURES / "spyglass_page.html").read_bytes())
    responses.get(
        f"{GCSWEB_BASE}{GCS_PATH}/prowjob.json",
        body=(FIXTURES / "prowjob.json").read_bytes(),
    )
    responses.get(
        f"{GCSWEB_BASE}{GCS_PATH}/artifacts/ci-operator-step-graph.json",
        body=(FIXTURES / "step_graph.json").read_bytes(),
    )
    responses.get(
        f"{GCSWEB_BASE}{GCS_PATH}/artifacts/junit_operator.xml",
        body=(FIXTURES / "junit_operator.xml").read_bytes(),
    )


class TestImportJob:
    @responses.activate
    def test_happy_path_creates_job_json_with_correct_metadata(self, tmp_path):
        _mock_all()

        job_dir = import_job(SPYGLASS_URL, tmp_path)

        assert job_dir == tmp_path / BUILD_ID
        job = json.loads((job_dir / "job.json").read_text())
        assert job["spyglass"] == SPYGLASS_URL
        assert job["build_id"] == BUILD_ID
        assert job["job_name"] == "pull-ci-openshift-ci-tools-main-breaking-changes"
        assert job["job_type"] == "presubmit"
        assert job["pr_link"] == "https://github.com/openshift/ci-tools/pull/5151"
        assert job["gcs_path"] == GCS_PATH
        assert job["gcsweb_base"] == GCSWEB_BASE

    @responses.activate
    def test_steps_json_contains_recursive_hierarchy(self, tmp_path):
        _mock_all()

        job_dir = import_job(SPYGLASS_URL, tmp_path)

        steps = json.loads((job_dir / "steps.json").read_text())
        assert "[input:root]" not in steps
        assert steps["src"] == {"success": True}
        assert steps["breaking-changes"]["success"] is False
        assert steps["breaking-changes"]["substeps"]["setup"]["success"] is True
        assert steps["breaking-changes"]["substeps"]["breaking-changes"]["success"] is False

    @responses.activate
    def test_idempotent_skips_refetch(self, tmp_path):
        _mock_all()

        job_dir = import_job(SPYGLASS_URL, tmp_path)
        call_count_after_first = len(responses.calls)

        result = import_job(SPYGLASS_URL, tmp_path)

        assert result == job_dir
        assert len(responses.calls) == call_count_after_first

    @responses.activate
    def test_step_graph_404_raises_job_import_error(self, tmp_path):
        responses.get(SPYGLASS_URL, body=(FIXTURES / "spyglass_page.html").read_bytes())
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/ci-operator-step-graph.json",
            status=404,
        )

        with pytest.raises(JobImportError):
            import_job(SPYGLASS_URL, tmp_path)

        assert not (tmp_path / BUILD_ID / "job.json").exists()

    def test_public_api_is_restricted(self):
        import dredge.import_job as module

        assert set(module.__all__) == {"import_job", "JobImportError"}
