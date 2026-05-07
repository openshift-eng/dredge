import tarfile
from pathlib import Path

from ..fetcher import fetch_url
from . import _gcsweb
from ._types import ArtifactEntry, ArtifactType


class Step:
    def __init__(
        self,
        *,
        name: str,
        success: bool,
        gcs_path: str,
        gcsweb_base: str,
        job_dir: str | Path,
        test_name: str | None = None,
    ):
        self.name = name
        self.success = success
        self._gcs_path = gcs_path
        self._gcsweb_base = gcsweb_base
        self._job_dir = Path(job_dir)
        self.test_name = test_name

    @property
    def step_path(self) -> str:
        if self.test_name:
            return f"{self.test_name}/{self.name}"
        return self.name

    @property
    def local_dir(self) -> Path:
        return self._job_dir / self.step_path

    def _artifact_base_url(self) -> str:
        return f"{self._gcsweb_base}{self._gcs_path}/artifacts/{self.step_path}"

    def get_log(self) -> Path:
        dest = self.local_dir / "build-log.txt"
        if dest.exists():
            return dest
        url = f"{self._artifact_base_url()}/build-log.txt"
        _gcsweb.download(url, dest)
        return dest

    def list_artifacts(self, path: str = "/") -> list[ArtifactEntry]:
        path = path.strip("/")
        if path:
            url = f"{self._artifact_base_url()}/artifacts/{path}/"
        else:
            url = f"{self._artifact_base_url()}/artifacts/"
        return _gcsweb.list_dir(url)

    def get_artifact(self, path: str, recursive: bool = False) -> Path:
        dest = self.local_dir / "artifacts" / path
        if recursive:
            self._download_tree(path)
            return dest
        if dest.exists():
            return dest
        url = f"{self._artifact_base_url()}/artifacts/{path}"
        _gcsweb.download(url, dest)
        return dest

    def extract_artifact(self, path: str) -> Path:
        extract_dir = self.local_dir / "artifacts" / Path(path).stem
        if extract_dir.exists() and any(extract_dir.iterdir()):
            return extract_dir
        url = f"{self._artifact_base_url()}/artifacts/{path}"
        extract_dir.mkdir(parents=True, exist_ok=True)
        with fetch_url(url) as body, tarfile.open(fileobj=body, mode="r|gz") as tar:
            tar.extractall(path=extract_dir)
        return extract_dir

    def _download_tree(self, path: str) -> None:
        for entry in self.list_artifacts(path):
            child_path = f"{path.strip('/')}/{entry.filename}"
            if entry.type == ArtifactType.DIR:
                self._download_tree(child_path)
            else:
                dest = self.local_dir / "artifacts" / child_path
                if dest.exists():
                    continue
                url = f"{self._artifact_base_url()}/artifacts/{child_path}"
                _gcsweb.download(url, dest)
