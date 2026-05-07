import gzip
import io
import json
import tarfile
from pathlib import Path

import responses

from dredge.commands import (
    _download_must_gather,
    _resolve_step,
    cmd_step_extract,
    cmd_step_get,
    cmd_step_log,
    cmd_step_ls,
)
from dredge.prow import Job

FIXTURES = Path(__file__).parent / "fixtures"
GCSWEB_BASE = "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/"
GCS_PATH = "test-bucket/pr-logs/pull/org_repo/123/job-name/9999"


def _create_job(tmp_path: Path) -> tuple[Path, str]:
    build_id = "9999"
    job_dir = tmp_path / build_id
    job_dir.mkdir()
    job_data = {
        "spyglass": "https://prow.ci/view/gs/" + GCS_PATH,
        "build_id": build_id,
        "job_name": "job-name",
        "job_type": "presubmit",
        "pr_link": "https://github.com/org/repo/pull/123",
        "gcs_path": GCS_PATH,
        "gcsweb_base": GCSWEB_BASE,
    }
    steps_data = {
        "e2e-aws": {
            "success": False,
            "substeps": {
                "openshift-e2e-test": {"success": False},
                "gather-must-gather": {"success": True},
            },
        },
    }
    (job_dir / "job.json").write_text(json.dumps(job_data))
    (job_dir / "steps.json").write_text(json.dumps(steps_data))
    return tmp_path, build_id


class TestDownloadMustGather:
    @responses.activate
    def test_extracts_to_step_artifact_dir(self, tmp_path):
        dredge_dir, build_id = _create_job(tmp_path)
        job = Job(dredge_dir / build_id)

        url = (
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/e2e-aws/"
            f"gather-must-gather/artifacts/must-gather.tar"
        )
        responses.get(url, body=(FIXTURES / "must-gather.tar.gz").read_bytes())

        _download_must_gather(job)

        step_artifact_dir = (
            dredge_dir / build_id / "e2e-aws" / "gather-must-gather" / "artifacts" / "must-gather"
        )
        assert step_artifact_dir.is_dir()
        assert (
            step_artifact_dir / "must-gather" / "cluster-scoped-resources" / "core" / "nodes.yaml"
        ).exists()
        assert (
            step_artifact_dir / "must-gather" / "namespaces" / "openshift-machine-api" / "pods.yaml"
        ).exists()

        # Must NOT be extracted to the job root
        assert not (dredge_dir / build_id / "must-gather").exists()


class TestResolveStep:
    def test_top_level(self, tmp_path):
        dredge_dir, build_id = _create_job(tmp_path)
        job = Job(dredge_dir / build_id)
        step = _resolve_step(job, "e2e-aws")
        assert step.step_path == "e2e-aws"

    def test_inner_step(self, tmp_path):
        dredge_dir, build_id = _create_job(tmp_path)
        job = Job(dredge_dir / build_id)
        step = _resolve_step(job, "e2e-aws/openshift-e2e-test")
        assert step.step_path == "e2e-aws/openshift-e2e-test"


class TestStepLs:
    @responses.activate
    def test_prints_json(self, tmp_path, capsys):
        dredge_dir, build_id = _create_job(tmp_path)
        gcs_prefix = f"/gcs/{GCS_PATH}/artifacts/e2e-aws/openshift-e2e-test/artifacts"
        html = (
            f'<html><ul>'
            f'<li><a href="{gcs_prefix}/junit/">junit/</a></li>'
            f'</ul></html>'
        )
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/e2e-aws/openshift-e2e-test/artifacts/"
        responses.get(url, body=html.encode())

        cmd_step_ls(dredge_dir, build_id, "e2e-aws/openshift-e2e-test", "/")

        output = json.loads(capsys.readouterr().out)
        assert len(output) == 1
        assert output[0]["filename"] == "junit"
        assert output[0]["type"] == "dir"


class TestStepLog:
    @responses.activate
    def test_prints_path(self, tmp_path, capsys):
        dredge_dir, build_id = _create_job(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/e2e-aws/openshift-e2e-test/build-log.txt"
        responses.get(url, body=b"log content")

        cmd_step_log(dredge_dir, build_id, "e2e-aws/openshift-e2e-test")

        output = capsys.readouterr().out.strip()
        result = Path(output)
        assert result.exists()
        assert result.read_text() == "log content"


class TestStepGet:
    @responses.activate
    def test_prints_path(self, tmp_path, capsys):
        dredge_dir, build_id = _create_job(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/e2e-aws/openshift-e2e-test/artifacts/foo.txt"
        responses.get(url, body=b"file content")

        cmd_step_get(dredge_dir, build_id, "e2e-aws/openshift-e2e-test", "foo.txt", False)

        output = capsys.readouterr().out.strip()
        result = Path(output)
        assert result.exists()
        assert result.read_text() == "file content"


class TestStepExtract:
    @responses.activate
    def test_extracts_and_prints_path(self, tmp_path, capsys):
        dredge_dir, build_id = _create_job(tmp_path)
        url = (
            f"{GCSWEB_BASE}{GCS_PATH}/artifacts/e2e-aws/"
            f"gather-must-gather/artifacts/must-gather.tar"
        )

        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz, tarfile.open(fileobj=gz, mode="w") as tar:
                info = tarfile.TarInfo(name="data.txt")
                content = b"hello"
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
        responses.get(url, body=buf.getvalue())

        cmd_step_extract(
            dredge_dir, build_id, "e2e-aws/gather-must-gather", "must-gather.tar"
        )

        output = capsys.readouterr().out.strip()
        result = Path(output)
        assert result.exists()
        assert (result / "data.txt").read_text() == "hello"
