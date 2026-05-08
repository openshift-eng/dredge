import gzip
import io
import tarfile
from pathlib import Path

import pytest
import responses

from dredge.prow import ArtifactEntry, ArtifactType, Step
from dredge.prow._step import BuildStepArtifactError

FIXTURES = Path(__file__).parent / "fixtures"

GCS_PATH = "test-bucket/pr-logs/pull/org_repo/123/job-name/9999"
GCSWEB_BASE = "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/"


def _make_step(tmp_path, *, name="openshift-e2e-test", test_name=None, success=False, step_type=None):
    return Step(
        name=name,
        success=success,
        gcs_path=GCS_PATH,
        gcsweb_base=GCSWEB_BASE,
        job_dir=tmp_path,
        test_name=test_name,
        step_type=step_type,
    )


class TestStepPath:
    def test_top_level(self, tmp_path):
        step = _make_step(tmp_path, name="src")
        assert step.step_path == "src"

    def test_inner(self, tmp_path):
        step = _make_step(tmp_path, name="openshift-e2e-test", test_name="e2e-aws")
        assert step.step_path == "e2e-aws/openshift-e2e-test"


class TestGetLog:
    @responses.activate
    def test_downloads_build_log(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/build-log.txt"
        responses.get(url, body=b"log content here")

        result = step.get_log()

        assert result == tmp_path / step.name / "build-log.txt"
        assert result.read_text() == "log content here"

    @responses.activate
    def test_inner_step_url(self, tmp_path):
        step = _make_step(tmp_path, name="openshift-e2e-test", test_name="e2e-aws")
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/e2e-aws/openshift-e2e-test/build-log.txt"
        responses.get(url, body=b"inner log")

        result = step.get_log()

        assert result == tmp_path / "e2e-aws" / "openshift-e2e-test" / "build-log.txt"
        assert result.read_text() == "inner log"

    @responses.activate
    def test_container_test_step(self, tmp_path):
        step = _make_step(tmp_path, name="test")
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/test/build-log.txt"
        responses.get(url, body=b"unit test output")

        result = step.get_log()

        assert result == tmp_path / "test" / "build-log.txt"
        assert result.read_text() == "unit test output"

    @responses.activate
    def test_idempotent(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/build-log.txt"
        responses.get(url, body=b"log content")

        step.get_log()
        assert len(responses.calls) == 1

        step.get_log()
        assert len(responses.calls) == 1


class TestBuildStepGetLog:
    @responses.activate
    def test_downloads_top_level_prow_log(self, tmp_path):
        step = _make_step(tmp_path, name="azure-cloud-controller-manager", step_type="build")
        url = f"{GCSWEB_BASE}{GCS_PATH}/build-log.txt"
        responses.get(url, body=b"ci-operator full output")

        result = step.get_log()

        assert result.read_text() == "ci-operator full output"
        assert result.is_symlink()
        assert result.resolve() == (tmp_path / "build-log.txt").resolve()

    @responses.activate
    def test_deduplicates_across_build_steps(self, tmp_path):
        url = f"{GCSWEB_BASE}{GCS_PATH}/build-log.txt"
        responses.get(url, body=b"ci-operator full output")

        step1 = _make_step(tmp_path, name="bin", step_type="build")
        step2 = _make_step(tmp_path, name="azure-ccm", step_type="build")

        step1.get_log()
        step2.get_log()

        assert len(responses.calls) == 1
        assert (tmp_path / "bin" / "build-log.txt").read_text() == "ci-operator full output"
        assert (tmp_path / "azure-ccm" / "build-log.txt").read_text() == "ci-operator full output"


class TestBuildStepArtifacts:
    def test_list_artifacts_returns_empty(self, tmp_path):
        step = _make_step(tmp_path, name="bin", step_type="build")
        assert step.list_artifacts() == []

    def test_get_artifact_raises(self, tmp_path):
        step = _make_step(tmp_path, name="bin", step_type="build")
        with pytest.raises(BuildStepArtifactError, match="no artifact directory"):
            step.get_artifact("some-file.txt")

    def test_extract_artifact_raises(self, tmp_path):
        step = _make_step(tmp_path, name="bin", step_type="build")
        with pytest.raises(BuildStepArtifactError, match="no artifact directory"):
            step.extract_artifact("some.tar")


class TestListArtifacts:
    @responses.activate
    def test_returns_structured_entries(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/"
        responses.get(url, body=(FIXTURES / "gcsweb_dir.html").read_bytes())

        entries = step.list_artifacts()

        dirs = [e for e in entries if e.type == ArtifactType.DIR]
        files = [e for e in entries if e.type == ArtifactType.FILE]
        assert {d.filename for d in dirs} == {"junit", "must-gather"}
        assert {f.filename for f in files} == {
            "e2e-events_20250101-120000.json",
            "junit_e2e_20250101-120000.xml",
        }
        for e in entries:
            assert isinstance(e, ArtifactEntry)

    @responses.activate
    def test_file_sizes_populated(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/"
        responses.get(url, body=(FIXTURES / "gcsweb_dir.html").read_bytes())

        entries = step.list_artifacts()

        files = {e.filename: e.size for e in entries if e.type == ArtifactType.FILE}
        assert files["e2e-events_20250101-120000.json"] == 1048576
        assert files["junit_e2e_20250101-120000.xml"] == 524288
        for e in entries:
            if e.type == ArtifactType.DIR:
                assert e.size is None

    @responses.activate
    def test_default_path_is_root(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/"
        responses.get(url, body=(FIXTURES / "gcsweb_dir.html").read_bytes())

        step.list_artifacts()

        assert responses.calls[0].request.url == url

    @responses.activate
    def test_path_normalization(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/junit/"
        gcs_prefix = f"/gcs/{GCS_PATH}/artifacts/{step.name}/artifacts/junit"
        html = f'<html><ul><li><a href="{gcs_prefix}/results.xml">results.xml</a></li></ul></html>'
        responses.get(url, body=html.encode())

        step.list_artifacts(path="/junit/")

        assert responses.calls[0].request.url == url

    @responses.activate
    def test_subdirectory(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/junit/"
        gcs_prefix = f"/gcs/{GCS_PATH}/artifacts/{step.name}/artifacts/junit"
        html = (
            f"<html><ul>"
            f'<li><a href="/gcs/{GCS_PATH}/artifacts/{step.name}/artifacts/">..</a></li>'
            f'<li><a href="{gcs_prefix}/results.xml">results.xml</a></li>'
            f"</ul></html>"
        )
        responses.get(url, body=html.encode())

        entries = step.list_artifacts(path="junit")

        assert len(entries) == 1
        assert entries[0] == ArtifactEntry(
            filename="results.xml", size=None, type=ArtifactType.FILE
        )


class TestGetArtifact:
    @responses.activate
    def test_downloads_artifact(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/must-gather.tar"
        responses.get(url, body=b"tar content")

        result = step.get_artifact("must-gather.tar")

        assert result == tmp_path / step.name / "artifacts" / "must-gather.tar"
        assert result.read_bytes() == b"tar content"

    @responses.activate
    def test_idempotent(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/must-gather.tar"
        responses.get(url, body=b"tar content")

        step.get_artifact("must-gather.tar")
        assert len(responses.calls) == 1

        step.get_artifact("must-gather.tar")
        assert len(responses.calls) == 1


def _make_tar_gz(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with gzip.GzipFile(fileobj=buf, mode="wb") as gz, tarfile.open(fileobj=gz, mode="w") as tar:
            for name, content in files.items():
                info = tarfile.TarInfo(name=name)
                info.size = len(content)
                tar.addfile(info, io.BytesIO(content))
    return buf.getvalue()


class TestExtractArtifact:
    @responses.activate
    def test_extracts_tar_gz(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/must-gather.tar"
        tar_data = _make_tar_gz({"hello.txt": b"world"})
        responses.get(url, body=tar_data)

        result = step.extract_artifact("must-gather.tar")

        assert result == tmp_path / step.name / "artifacts" / "must-gather"
        assert (result / "hello.txt").read_text() == "world"

    @responses.activate
    def test_error_on_non_tar(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/bad.tar"
        responses.get(url, body=b"this is not a tar file")

        import pytest

        with pytest.raises(tarfile.TarError):
            step.extract_artifact("bad.tar")

    @responses.activate
    def test_idempotent(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/must-gather.tar"
        tar_data = _make_tar_gz({"hello.txt": b"world"})
        responses.get(url, body=tar_data)

        step.extract_artifact("must-gather.tar")
        assert len(responses.calls) == 1

        step.extract_artifact("must-gather.tar")
        assert len(responses.calls) == 1


class TestGetArtifactRecursive:
    @responses.activate
    def test_recursive_download(self, tmp_path):
        step = _make_step(tmp_path)
        base = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts"
        gcs_prefix = f"/gcs/{GCS_PATH}/artifacts/{step.name}/artifacts"

        top_html = (
            f'<html><ul>'
            f'<li><a href="{gcs_prefix}/data/sub/">sub/</a></li>'
            f'<li><a href="{gcs_prefix}/data/a.txt">a.txt</a></li>'
            f'</ul></html>'
        )
        sub_html = (
            f'<html><ul>'
            f'<li><a href="{gcs_prefix}/data/sub/b.txt">b.txt</a></li>'
            f'</ul></html>'
        )
        responses.get(f"{base}/data/", body=top_html.encode())
        responses.get(f"{base}/data/sub/", body=sub_html.encode())
        responses.get(f"{base}/data/a.txt", body=b"file-a")
        responses.get(f"{base}/data/sub/b.txt", body=b"file-b")

        result = step.get_artifact("data", recursive=True)

        assert result == tmp_path / step.name / "artifacts" / "data"
        assert (tmp_path / step.name / "artifacts" / "data" / "a.txt").read_bytes() == b"file-a"
        data_dir = tmp_path / step.name / "artifacts" / "data"
        assert (data_dir / "sub" / "b.txt").read_bytes() == b"file-b"

    @responses.activate
    def test_recursive_idempotent(self, tmp_path):
        step = _make_step(tmp_path)
        base = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts"
        gcs_prefix = f"/gcs/{GCS_PATH}/artifacts/{step.name}/artifacts"

        top_html = (
            f'<html><ul>'
            f'<li><a href="{gcs_prefix}/data/a.txt">a.txt</a></li>'
            f'</ul></html>'
        )
        responses.get(f"{base}/data/", body=top_html.encode())
        responses.get(f"{base}/data/a.txt", body=b"file-a")

        step.get_artifact("data", recursive=True)
        call_count = len(responses.calls)

        responses.get(f"{base}/data/", body=top_html.encode())
        step.get_artifact("data", recursive=True)
        # Listed again but did not re-download the file
        assert len(responses.calls) == call_count + 1


class TestArtifactEntry:
    def test_serializes_to_json(self):
        import dataclasses
        import json

        entry = ArtifactEntry(filename="test.txt", size=1024, type=ArtifactType.FILE)
        result = json.loads(json.dumps(dataclasses.asdict(entry)))
        assert result == {"filename": "test.txt", "size": 1024, "type": "file"}

    def test_str_comparison(self):
        entry = ArtifactEntry(filename="subdir", size=None, type=ArtifactType.DIR)
        assert entry.type == "dir"


class TestPublicAPI:
    def test_step_in_prow_all(self):
        import dredge.prow as module

        assert "Step" in module.__all__

    def test_artifact_entry_in_prow_all(self):
        import dredge.prow as module

        assert "ArtifactEntry" in module.__all__
        assert "ArtifactType" in module.__all__
