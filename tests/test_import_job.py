import json
from pathlib import Path

import pytest
import responses

from dredge.prow import Job, JobImportError, import_from_spyglass

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


class TestImportJob:
    @responses.activate
    def test_happy_path_returns_job_with_correct_metadata(self, tmp_path):
        _mock_all()

        job = import_from_spyglass(SPYGLASS_URL, tmp_path)

        assert isinstance(job, Job)
        assert job.job_dir == tmp_path / BUILD_ID
        assert job.spyglass == SPYGLASS_URL
        assert job.build_id == BUILD_ID
        assert job.job_name == "pull-ci-openshift-ci-tools-main-breaking-changes"
        assert job.job_type == "presubmit"
        assert job.pr_link == "https://github.com/openshift/ci-tools/pull/5151"
        assert job.gcs_path == GCS_PATH
        assert job.gcsweb_base == GCSWEB_BASE

    @responses.activate
    def test_writes_steps_json_with_recursive_hierarchy(self, tmp_path):
        _mock_all()

        job = import_from_spyglass(SPYGLASS_URL, tmp_path)

        steps = json.loads((job.job_dir / "steps.json").read_text())
        assert "[input:root]" not in steps
        assert steps["src"]["status"] == "passed"
        assert steps["src"]["type"] == "build"
        assert steps["breaking-changes"]["status"] == "failed"
        assert steps["breaking-changes"]["type"] == "test"
        assert steps["breaking-changes"]["substeps"]["setup"]["status"] == "passed"
        assert steps["breaking-changes"]["substeps"]["breaking-changes"]["status"] == "failed"

    @responses.activate
    def test_idempotent_skips_refetch(self, tmp_path):
        _mock_all()

        job = import_from_spyglass(SPYGLASS_URL, tmp_path)
        call_count_after_first = len(responses.calls)

        job2 = import_from_spyglass(SPYGLASS_URL, tmp_path)

        assert isinstance(job2, Job)
        assert job2.job_dir == job.job_dir
        assert len(responses.calls) == call_count_after_first

    @responses.activate
    def test_step_graph_404_raises_job_import_error(self, tmp_path):
        responses.get(SPYGLASS_URL, body=(FIXTURES / "spyglass_page.html").read_bytes())
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/ci-operator-step-graph.json",
            status=404,
        )

        with pytest.raises(JobImportError):
            import_from_spyglass(SPYGLASS_URL, tmp_path)

        assert not (tmp_path / BUILD_ID / "job.json").exists()

    @responses.activate
    def test_substeps_from_step_graph_skips_junit_fetch(self, tmp_path):
        responses.get(SPYGLASS_URL, body=(FIXTURES / "spyglass_page.html").read_bytes())
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/prowjob.json",
            body=(FIXTURES / "prowjob.json").read_bytes(),
        )
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/ci-operator-step-graph.json",
            body=(FIXTURES / "step_graph.json").read_bytes(),
        )

        job = import_from_spyglass(SPYGLASS_URL, tmp_path)

        steps = json.loads((job.job_dir / "steps.json").read_text())
        assert steps["breaking-changes"]["substeps"]["setup"]["status"] == "passed"
        assert steps["breaking-changes"]["substeps"]["breaking-changes"]["status"] == "failed"
        fetched_urls = [c.request.url for c in responses.calls]
        assert not any("junit_operator.xml" in u for u in fetched_urls)

    @responses.activate
    def test_falls_back_to_junit_when_no_substeps(self, tmp_path):
        responses.get(SPYGLASS_URL, body=(FIXTURES / "spyglass_page.html").read_bytes())
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/prowjob.json",
            body=(FIXTURES / "prowjob.json").read_bytes(),
        )
        old_step_graph = json.loads((FIXTURES / "step_graph.json").read_text())
        for step in old_step_graph:
            step.pop("substeps", None)
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/ci-operator-step-graph.json",
            json=old_step_graph,
        )
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/junit_operator.xml",
            body=(FIXTURES / "junit_operator.xml").read_bytes(),
        )

        job = import_from_spyglass(SPYGLASS_URL, tmp_path)

        steps = json.loads((job.job_dir / "steps.json").read_text())
        assert steps["breaking-changes"]["substeps"]["setup"]["status"] == "passed"
        assert steps["breaking-changes"]["substeps"]["breaking-changes"]["status"] == "failed"

    @responses.activate
    def test_container_test_step_renamed_to_test(self, tmp_path):
        responses.get(SPYGLASS_URL, body=(FIXTURES / "spyglass_page.html").read_bytes())
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/prowjob.json",
            body=(FIXTURES / "prowjob.json").read_bytes(),
        )
        step_graph = [
            {"name": "[input:root]"},
            {
                "name": "src",
                "description": "Clone the correct source code into an image and tag it as src",
                "started_at": "2025-01-15T10:00:00Z",
                "finished_at": "2025-01-15T10:05:00Z",
                "manifests": [{"kind": "Build", "metadata": {"name": "src-amd64"}}],
            },
            {
                "name": "unit",
                "description": "Run test unit",
                "failed": True,
                "started_at": "2025-01-15T10:05:00Z",
                "finished_at": "2025-01-15T10:06:00Z",
            },
        ]
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/ci-operator-step-graph.json",
            json=step_graph,
        )
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/junit_operator.xml",
            body=b'<testsuite><testcase name="Run unit test"/></testsuite>',
        )

        job = import_from_spyglass(SPYGLASS_URL, tmp_path)

        steps = json.loads((job.job_dir / "steps.json").read_text())
        assert "unit" not in steps
        assert "test" in steps
        assert steps["test"]["status"] == "failed"
        assert steps["src"]["status"] == "passed"

    @responses.activate
    def test_skipped_steps_have_status_skipped(self, tmp_path):
        """Steps that never ran (null started_at) get status=skipped."""
        responses.get(SPYGLASS_URL, body=(FIXTURES / "spyglass_page.html").read_bytes())
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/prowjob.json",
            body=(FIXTURES / "prowjob.json").read_bytes(),
        )
        step_graph = [
            {"name": "[input:root]"},
            {
                "name": "src",
                "description": "Clone the correct source code into an image and tag it as src",
                "started_at": "2025-01-15T10:00:00Z",
                "finished_at": "2025-01-15T10:05:00Z",
                "manifests": [{"kind": "Build", "metadata": {"name": "src-amd64"}}],
            },
            {
                "name": "bin",
                "description": "Store build results into a layer on top of src and save as bin",
                "failed": True,
                "started_at": "2025-01-15T10:05:00Z",
                "finished_at": "2025-01-15T10:06:00Z",
                "manifests": [{"kind": "Build", "metadata": {"name": "bin-amd64"}}],
            },
            {
                "name": "rpms",
                "description": "Store build results into a layer on top of bin and save as rpms",
            },
            {
                "name": "e2e-test",
                "description": "Run multi-stage test e2e-test",
            },
        ]
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/ci-operator-step-graph.json",
            json=step_graph,
        )

        job = import_from_spyglass(SPYGLASS_URL, tmp_path)

        steps = json.loads((job.job_dir / "steps.json").read_text())
        assert steps["rpms"]["status"] == "skipped"
        assert steps["rpms"]["type"] == "build"
        assert steps["e2e-test"]["status"] == "skipped"
        assert steps["e2e-test"]["type"] == "test"
        assert steps["bin"]["status"] == "failed"
        assert steps["bin"]["type"] == "build"

    @responses.activate
    def test_build_steps_have_type_build_and_are_not_renamed(self, tmp_path):
        """Image build failures must appear with their real name and type=build."""
        responses.get(SPYGLASS_URL, body=(FIXTURES / "spyglass_page.html").read_bytes())
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/prowjob.json",
            body=(FIXTURES / "prowjob.json").read_bytes(),
        )
        step_graph = [
            {"name": "[input:root]"},
            {
                "name": "src",
                "description": "Clone the correct source code into an image and tag it as src",
                "started_at": "2025-01-15T10:00:00Z",
                "finished_at": "2025-01-15T10:05:00Z",
                "manifests": [{"kind": "Build", "metadata": {"name": "src-amd64"}}],
            },
            {
                "name": "azure-cloud-controller-manager",
                "description": "Build image azure-cloud-controller-manager from the repository",
                "failed": True,
                "started_at": "2025-01-15T10:05:00Z",
                "finished_at": "2025-01-15T10:07:00Z",
                "manifests": [
                    {
                        "kind": "Build",
                        "metadata": {"name": "azure-cloud-controller-manager-amd64"},
                    }
                ],
            },
            {
                "name": "e2e-azure-ovn-upgrade",
                "description": "Run multi-stage test e2e-azure-ovn-upgrade",
            },
        ]
        responses.get(
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/ci-operator-step-graph.json",
            json=step_graph,
        )

        job = import_from_spyglass(SPYGLASS_URL, tmp_path)

        steps = json.loads((job.job_dir / "steps.json").read_text())
        assert steps["src"]["type"] == "build"
        assert steps["src"]["status"] == "passed"
        assert steps["azure-cloud-controller-manager"]["type"] == "build"
        assert steps["azure-cloud-controller-manager"]["status"] == "failed"
        assert "azure-cloud-controller-manager" in steps

    def test_public_api_is_restricted(self):
        import dredge.prow as module

        assert set(module.__all__) == {
            "ArtifactEntry",
            "ArtifactType",
            "import_from_spyglass",
            "Job",
            "JobImportError",
            "Step",
        }
