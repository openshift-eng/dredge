import json
import re
import xml.etree.ElementTree as ET
from typing import Any
from urllib.parse import urlparse

from ..fetcher import fetch_url


def parse_spyglass_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    path = parsed.path
    build_id = path.rstrip("/").split("/")[-1]
    return build_id, path


def extract_prow_base_url(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def spyglass_to_gcs_path(spyglass_link: str) -> str:
    for prefix in ("/view/gs/", "/view/gcs/"):
        if spyglass_link.startswith(prefix):
            return spyglass_link[len(prefix) :]
    return spyglass_link


def discover_gcsweb_base(prow_base_url: str, spyglass_link: str) -> str:
    url = f"{prow_base_url}{spyglass_link}"
    with fetch_url(url) as body:
        html = body.read().decode()
    match = re.search(r'(https?://[^"\s]+/gcs/)[^"\s]+', html)
    if not match:
        raise ValueError("Could not discover gcsweb URL from Spyglass page")
    return match.group(1)


def fetch_step_graph(gcsweb_base: str, gcs_path: str) -> Any:
    url = f"{gcsweb_base}{gcs_path}/artifacts/ci-operator-step-graph.json"
    with fetch_url(url) as body:
        return json.loads(body.read().decode())


def fetch_job_spec(gcsweb_base: str, gcs_path: str) -> dict[str, str | None]:
    url = f"{gcsweb_base}{gcs_path}/prowjob.json"
    with fetch_url(url) as body:
        data = json.loads(body.read().decode())
    spec = data.get("spec", {})
    refs = spec.get("refs", {})
    pulls = refs.get("pulls", [])
    return {
        "job": spec.get("job"),
        "type": spec.get("type"),
        "pr_link": pulls[0].get("link") if pulls else None,
    }


def extract_steps(step_graph: Any) -> list[dict[str, Any]]:
    steps = []
    for s in step_graph:
        name = s.get("name", "")
        if name.startswith("["):
            continue
        inner: dict[str, Any] = {}
        prefix = name + "-"
        for sub in s.get("substeps", []):
            sub_name = sub.get("name", "")
            stripped = sub_name[len(prefix) :] if sub_name.startswith(prefix) else sub_name
            inner[stripped] = {"success": not sub.get("failed", False)}
        steps.append(
            {
                "name": name,
                "success": not s.get("failed", False),
                "inner_steps": inner,
            }
        )
    return steps


def fetch_junit_steps(gcsweb_base: str, gcs_path: str) -> dict[str, dict[str, object]]:
    # Fallback for older jobs without substeps in the step graph.
    url = f"{gcsweb_base}{gcs_path}/artifacts/junit_operator.xml"
    with fetch_url(url) as body:
        xml_text = body.read().decode()

    root = ET.fromstring(xml_text)
    steps: dict[str, dict[str, object]] = {}

    for tc in root.iter("testcase"):
        name = tc.get("name", "")
        m = re.match(r"Run multi-stage test (\S+) - (\S+) container test", name)
        if not m:
            continue
        test_name, full_step = m.group(1), m.group(2)
        prefix = test_name + "-"
        inner_step = full_step[len(prefix) :] if full_step.startswith(prefix) else full_step
        failed = tc.find("failure") is not None
        steps.setdefault(test_name, {})[inner_step] = {"success": not failed}

    return steps


def apply_inner_steps(
    steps: list[dict[str, Any]], junit_steps: dict[str, dict[str, object]]
) -> None:
    for step in steps:
        inner = junit_steps.get(step["name"])
        if inner:
            step["inner_steps"] = inner
