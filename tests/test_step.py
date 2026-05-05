from pathlib import Path

import responses

from dredge.step import Step

FIXTURES = Path(__file__).parent / "fixtures"

GCS_PATH = "test-bucket/pr-logs/pull/org_repo/123/job-name/9999"
GCSWEB_BASE = "https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/"


def _make_step(tmp_path, *, name="openshift-e2e-test", test_name=None, success=False):
    return Step(
        name=name,
        success=success,
        gcs_path=GCS_PATH,
        gcsweb_base=GCSWEB_BASE,
        job_dir=tmp_path,
        test_name=test_name,
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
    def test_idempotent(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/build-log.txt"
        responses.get(url, body=b"log content")

        step.get_log()
        assert len(responses.calls) == 1

        step.get_log()
        assert len(responses.calls) == 1


class TestListArtifacts:
    @responses.activate
    def test_returns_structured_entries(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/"
        responses.get(url, body=(FIXTURES / "gcsweb_dir.html").read_bytes())

        entries = step.list_artifacts()

        dirs = [e for e in entries if e["type"] == "dir"]
        files = [e for e in entries if e["type"] == "file"]
        assert {d["filename"] for d in dirs} == {"junit", "must-gather"}
        assert {f["filename"] for f in files} == {
            "e2e-events_20250101-120000.json",
            "junit_e2e_20250101-120000.xml",
        }
        for e in entries:
            assert "filename" in e
            assert "size" in e
            assert "type" in e

    @responses.activate
    def test_subdirectory(self, tmp_path):
        step = _make_step(tmp_path)
        url = f"{GCSWEB_BASE}{GCS_PATH}/artifacts/{step.name}/artifacts/junit/"
        gcs_prefix = f"/gcs/{GCS_PATH}/artifacts/{step.name}/artifacts/junit"
        html = (
            f'<html><ul>'
            f'<li><a href="/gcs/{GCS_PATH}/artifacts/{step.name}/artifacts/">..</a></li>'
            f'<li><a href="{gcs_prefix}/results.xml">results.xml</a></li>'
            f'</ul></html>'
        )
        responses.get(url, body=html.encode())

        entries = step.list_artifacts(path="junit")

        assert len(entries) == 1
        assert entries[0] == {"filename": "results.xml", "size": None, "type": "file"}


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


class TestPublicAPI:
    def test_all_is_restricted(self):
        import dredge.step as module

        assert set(module.__all__) == {"Step"}
