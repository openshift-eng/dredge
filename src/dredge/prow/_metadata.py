import json
import re
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

from ..fetcher import fetch_url


def parse_spyglass_url(url):
    parsed = urlparse(url)
    path = parsed.path
    build_id = path.rstrip("/").split("/")[-1]
    return build_id, path


def extract_prow_base_url(url):
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def spyglass_to_gcs_path(spyglass_link):
    for prefix in ("/view/gs/", "/view/gcs/"):
        if spyglass_link.startswith(prefix):
            return spyglass_link[len(prefix) :]
    return spyglass_link


def discover_gcsweb_base(prow_base_url, spyglass_link):
    url = f"{prow_base_url}{spyglass_link}"
    with fetch_url(url) as body:
        html = body.read().decode()
    match = re.search(r'(https?://[^"\s]+/gcs/)[^"\s]+', html)
    if not match:
        raise ValueError("Could not discover gcsweb URL from Spyglass page")
    return match.group(1)


def fetch_step_graph(gcsweb_base, gcs_path):
    url = f"{gcsweb_base}{gcs_path}/artifacts/ci-operator-step-graph.json"
    with fetch_url(url) as body:
        return json.loads(body.read().decode())


def fetch_job_spec(gcsweb_base, gcs_path):
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


def extract_steps(step_graph):
    steps = []
    for s in step_graph:
        name = s.get("name", "")
        if name.startswith("["):
            continue
        steps.append(
            {
                "name": name,
                "success": not s.get("failed", False),
                "inner_steps": {},
            }
        )
    return steps


def fetch_junit_steps(gcsweb_base, gcs_path):
    # Current primary source for inner steps. ci-tools PR #5151 will add
    # substeps to the step graph, which will eventually replace this.
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


def apply_inner_steps(steps, junit_steps):
    for step in steps:
        inner = junit_steps.get(step["name"])
        if inner:
            step["inner_steps"] = inner
