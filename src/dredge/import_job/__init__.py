import json
import logging
from pathlib import Path

from ..fetch_url import FetchError
from . import _metadata

__all__ = ["import_job", "JobImportError"]

logger = logging.getLogger(__name__)


class JobImportError(Exception):
    pass


def import_job(spyglass_url, output_dir):
    output_dir = Path(output_dir)

    build_id, spyglass_link = _metadata.parse_spyglass_url(spyglass_url)
    gcs_path = _metadata.spyglass_to_gcs_path(spyglass_link)
    prow_base_url = _metadata.extract_prow_base_url(spyglass_url)

    job_dir = output_dir / build_id

    if (job_dir / "job.json").exists() and (job_dir / "steps.json").exists():
        return job_dir

    try:
        gcsweb_base = _metadata.discover_gcsweb_base(prow_base_url, spyglass_link)
        step_graph = _metadata.fetch_step_graph(gcsweb_base, gcs_path)
        job_spec = _metadata.fetch_job_spec(gcsweb_base, gcs_path)
        steps = _metadata.extract_steps(step_graph)
        junit_steps = _metadata.fetch_junit_steps(gcsweb_base, gcs_path)
        _metadata.apply_inner_steps(steps, junit_steps)
    except (FetchError, ValueError) as e:
        raise JobImportError(str(e)) from e

    job_dir.mkdir(parents=True, exist_ok=True)

    first_steps_file = None
    steps_list = []
    for step in steps:
        entry = {"name": step["name"], "success": step["success"]}
        if step.get("inner_steps"):
            steps_file = f"{step['name']}.steps.json"
            entry["steps_file"] = steps_file
            (job_dir / steps_file).write_text(json.dumps(step["inner_steps"], indent=2))
            if first_steps_file is None:
                first_steps_file = steps_file
        steps_list.append(entry)

    job_data = {
        "spyglass": spyglass_url,
        "build_id": build_id,
        "job_name": job_spec["job"],
        "job_type": job_spec["type"],
        "pr_link": job_spec["pr_link"],
        "gcs_path": gcs_path,
        "gcsweb_base": gcsweb_base,
        "steps": steps_list,
    }
    (job_dir / "job.json").write_text(json.dumps(job_data, indent=2))
    (job_dir / "ci-operator-step-graph.json").write_text(json.dumps(step_graph, indent=2))

    if first_steps_file:
        steps_symlink = job_dir / "steps.json"
        if steps_symlink.exists() or steps_symlink.is_symlink():
            steps_symlink.unlink()
        steps_symlink.symlink_to(first_steps_file)

    return job_dir
