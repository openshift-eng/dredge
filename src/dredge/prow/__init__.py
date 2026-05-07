import json
import logging
from pathlib import Path

from ..fetcher import FetchError
from . import _metadata
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

    def step(self, name: str, inner_name: str | None = None) -> Step:
        if name not in self._steps_data:
            raise KeyError(f"Step not found: {name}")
        step_info = self._steps_data[name]

        if inner_name is None:
            return Step(
                name=name,
                success=step_info["success"],
                gcs_path=self.gcs_path,
                gcsweb_base=self.gcsweb_base,
                job_dir=self.job_dir,
            )

        substeps = step_info.get("substeps", {})
        if inner_name not in substeps:
            raise KeyError(f"Inner step not found: {name}/{inner_name}")
        return Step(
            name=inner_name,
            success=substeps[inner_name]["success"],
            gcs_path=self.gcs_path,
            gcsweb_base=self.gcsweb_base,
            job_dir=self.job_dir,
            test_name=name,
        )

    def steps(self) -> list[Step]:
        return [
            Step(
                name=name,
                success=info["success"],
                gcs_path=self.gcs_path,
                gcsweb_base=self.gcsweb_base,
                job_dir=self.job_dir,
            )
            for name, info in self._steps_data.items()
        ]

    def failed_steps(self) -> list[Step]:
        result = []
        for name, info in self._steps_data.items():
            for inner_name, inner_info in info.get("substeps", {}).items():
                if not inner_info["success"]:
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
        if not any(s["inner_steps"] for s in steps):
            junit_steps = _metadata.fetch_junit_steps(gcsweb_base, gcs_path)
            _metadata.apply_inner_steps(steps, junit_steps)
    except (FetchError, ValueError) as e:
        raise JobImportError(str(e)) from e

    job_dir.mkdir(parents=True, exist_ok=True)

    steps_hierarchy = {}
    for step in steps:
        entry = {"success": step["success"]}
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
