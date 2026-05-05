from pathlib import Path

from . import _gcsweb


class Step:
    def __init__(self, *, name, success, gcs_path, gcsweb_base, job_dir, test_name=None):
        self.name = name
        self.success = success
        self._gcs_path = gcs_path
        self._gcsweb_base = gcsweb_base
        self._job_dir = Path(job_dir)
        self.test_name = test_name

    @property
    def step_path(self):
        if self.test_name:
            return f"{self.test_name}/{self.name}"
        return self.name

    @property
    def local_dir(self):
        return self._job_dir / self.step_path

    def _artifact_base_url(self):
        return f"{self._gcsweb_base}{self._gcs_path}/artifacts/{self.step_path}"

    def get_log(self):
        dest = self.local_dir / "build-log.txt"
        if dest.exists():
            return dest
        url = f"{self._artifact_base_url()}/build-log.txt"
        _gcsweb.download(url, dest)
        return dest

    def list_artifacts(self, path="."):
        if path == ".":
            url = f"{self._artifact_base_url()}/artifacts/"
        else:
            url = f"{self._artifact_base_url()}/artifacts/{path}/"
        subdirs, files = _gcsweb.list_dir(url)
        entries = []
        for d in subdirs:
            entries.append({"filename": d, "size": None, "type": "dir"})
        for f in files:
            entries.append({"filename": f, "size": None, "type": "file"})
        return entries

    def get_artifact(self, path):
        dest = self.local_dir / "artifacts" / path
        if dest.exists():
            return dest
        url = f"{self._artifact_base_url()}/artifacts/{path}"
        _gcsweb.download(url, dest)
        return dest
