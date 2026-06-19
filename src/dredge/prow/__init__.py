import json
import logging
from pathlib import Path
from typing import Any

from ..fetcher import FetchError
from . import _gcsweb, _metadata
from ._step import Step
from ._types import ArtifactEntry, ArtifactType

__all__ = ["ArtifactEntry", "ArtifactType", "Job", "JobImportError", "Step", "import_from_spyglass"]

logger = logging.getLogger(__name__)


class JobImportError(Exception):
    pass


class Job:
    def __init__(self, job_dir: str | Path) -> None:
        job_dir = Path(job_dir)
        self.job_dir = job_dir

        job_data = json.loads((job_dir / "job.json").read_text())
        self.spyglass = job_data["spyglass"]
        self.build_id = job_data["build_id"]
        self.job_name = job_data["job_name"]
        self.job_type = job_data["job_type"]
        self.pr_link = job_data["pr_link"]
        self.gcs_path = job_data["gcs_path"]
        self.gcsweb_base = job_data["gcsweb_base"]

        self._steps_data = json.loads((job_dir / "steps.json").read_text())

    def _read_status(self, info: dict[str, Any]) -> bool:
        if "status" in info:
            return bool(info["status"] == "passed")
        return bool(info["success"])

    def step(self, name: str, inner_name: str | None = None) -> Step:
        if name not in self._steps_data:
            # Check if this might be an inner step name provided without the parent
            possible_matches = []
            for step_name, step_info in self._steps_data.items():
                substeps = step_info.get("substeps", {})
                if name in substeps:
                    possible_matches.append(f"{step_name}/{name}")

            if possible_matches:
                matches_str = ", ".join(possible_matches)
                raise KeyError(
                    f"Step not found: {name}\n"
                    f"Did you mean one of these? {matches_str}\n"
                    f"Use the full step path (e.g., parent/inner-step)"
                )

            available = ", ".join(sorted(self._steps_data.keys()))
            raise KeyError(
                f"Step not found: {name}\n"
                f"Available top-level steps: {available}"
            )
        step_info = self._steps_data[name]

        if inner_name is None:
            return Step(
                name=name,
                success=self._read_status(step_info),
                gcs_path=self.gcs_path,
                gcsweb_base=self.gcsweb_base,
                job_dir=self.job_dir,
                step_type=step_info.get("type"),
            )

        substeps = step_info.get("substeps", {})
        if inner_name not in substeps:
            available = ", ".join(sorted(substeps.keys()))
            raise KeyError(
                f"Inner step not found: {name}/{inner_name}\n"
                f"Available substeps in {name}: {available}"
            )
        return Step(
            name=inner_name,
            success=self._read_status(substeps[inner_name]),
            gcs_path=self.gcs_path,
            gcsweb_base=self.gcsweb_base,
            job_dir=self.job_dir,
            test_name=name,
        )

    def steps(self) -> list[Step]:
        return [
            Step(
                name=name,
                success=self._read_status(info),
                gcs_path=self.gcs_path,
                gcsweb_base=self.gcsweb_base,
                job_dir=self.job_dir,
                step_type=info.get("type"),
            )
            for name, info in self._steps_data.items()
        ]

    def get_root_junits(self) -> list[Path]:
        """Download JUnit XML files from the job root (not inside any step).

        These files are produced by ci-operator itself (e.g. junit_operator.xml
        recording step-level pass/fail) and by Prow (prowjob_junit.xml recording
        overall job completion). They live at the GCS root and under the
        artifacts/ prefix, outside any step directory.
        """
        downloaded: list[Path] = []
        # Scan both the job root and the artifacts/ prefix for junit XML files.
        prefixes = [
            (f"{self.gcsweb_base}{self.gcs_path}/", self.gcs_path),
            (f"{self.gcsweb_base}{self.gcs_path}/artifacts/", f"{self.gcs_path}/artifacts"),
        ]
        for url, gcs_prefix in prefixes:
            entries = _gcsweb.list_dir(url)
            for entry in entries:
                if (
                    entry.type == ArtifactType.FILE
                    and entry.filename.endswith(".xml")
                    and "junit" in entry.filename.lower()
                ):
                    dest = self.job_dir / entry.filename
                    if not dest.exists():
                        file_url = f"{self.gcsweb_base}{gcs_prefix}/{entry.filename}"
                        _gcsweb.download(file_url, dest)
                    downloaded.append(dest)
        return downloaded

    def failed_steps(self) -> list[Step]:
        result = []
        for name, info in self._steps_data.items():
            is_failed = info.get("status") == "failed" if "status" in info else not info["success"]
            substeps = info.get("substeps", {})
            if substeps:
                for inner_name, inner_info in substeps.items():
                    inner_failed = (
                        inner_info.get("status") == "failed"
                        if "status" in inner_info
                        else not inner_info["success"]
                    )
                    if inner_failed:
                        result.append(
                            Step(
                                name=inner_name,
                                success=False,
                                gcs_path=self.gcs_path,
                                gcsweb_base=self.gcsweb_base,
                                job_dir=self.job_dir,
                                test_name=name,
                            )
                        )
            elif is_failed:
                result.append(
                    Step(
                        name=name,
                        success=False,
                        gcs_path=self.gcs_path,
                        gcsweb_base=self.gcsweb_base,
                        job_dir=self.job_dir,
                        step_type=info.get("type"),
                    )
                )
        return result


def import_from_spyglass(spyglass_url: str, output_dir: str | Path) -> Job:
    output_dir = Path(output_dir)

    build_id, spyglass_link = _metadata.parse_spyglass_url(spyglass_url)
    gcs_path = _metadata.spyglass_to_gcs_path(spyglass_link)
    prow_base_url = _metadata.extract_prow_base_url(spyglass_url)

    job_dir = output_dir / build_id

    if (job_dir / "job.json").exists() and (job_dir / "steps.json").exists():
        return Job(job_dir)

    try:
        gcsweb_base = _metadata.discover_gcsweb_base(prow_base_url, spyglass_link)
        step_graph = _metadata.fetch_step_graph(gcsweb_base, gcs_path)
        job_spec = _metadata.fetch_job_spec(gcsweb_base, gcs_path)
        steps = _metadata.extract_steps(step_graph)
        test_steps = [s for s in steps if s["type"] == "test" and s["status"] != "skipped"]
        if test_steps and not any(s["inner_steps"] for s in test_steps):
            junit_steps = _metadata.fetch_junit_steps(gcsweb_base, gcs_path)
            _metadata.apply_inner_steps(steps, junit_steps)

        # ci-operator container tests always upload artifacts to "test/"
        # regardless of the test's configured name.
        # See: openshift/ci-tools pkg/steps/pod.go TestStep() calling PodStep("test", ...)
        if test_steps and not any(s["inner_steps"] for s in test_steps):
            for s in test_steps:
                s["name"] = "test"
    except (FetchError, ValueError) as e:
        raise JobImportError(str(e)) from e

    job_dir.mkdir(parents=True, exist_ok=True)

    steps_hierarchy = {}
    for step in steps:
        entry: dict[str, Any] = {"status": step["status"], "type": step["type"]}
        if step.get("inner_steps"):
            entry["substeps"] = step["inner_steps"]
        steps_hierarchy[step["name"]] = entry

    job_data = {
        "spyglass": spyglass_url,
        "build_id": build_id,
        "job_name": job_spec["job"],
        "job_type": job_spec["type"],
        "pr_link": job_spec["pr_link"],
        "gcs_path": gcs_path,
        "gcsweb_base": gcsweb_base,
    }
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2))
    (job_dir / "steps.json").write_text(json.dumps(steps_hierarchy, indent=2))
    (job_dir / "ci-operator-step-graph.json").write_text(json.dumps(step_graph, indent=2))

    return Job(job_dir)
