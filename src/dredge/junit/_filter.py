"""Filter JUnit XML testcases by status, lifecycle, and flakiness.

The output is structurally identical to the input — same XML schema, same
element hierarchy — with excluded testcases removed and testsuite counters
updated.

Lifecycle classification follows the Prow Spyglass convention:
  - blocking: ``<property name="lifecycle" value="blocking"/>`` or no
    lifecycle property (the default)
  - informing: ``<property name="lifecycle" value="informing"/>``

Flaky detection follows the Spyglass convention: a test is flaky when the
same (suite, classname, name) tuple appears more than once within a single
testsuite, with at least one passing and one failing entry.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from typing import Literal


def _get_lifecycle(tc: ET.Element) -> str:
    """Return the lifecycle value for a testcase, defaulting to 'blocking'."""
    props = tc.find("properties")
    if props is not None:
        for prop in props.findall("property"):
            if prop.get("name") == "lifecycle":
                return prop.get("value", "blocking")
    return "blocking"


def _get_status(tc: ET.Element) -> Literal["failed", "skipped", "passed"]:
    """Return the status of a testcase element."""
    if tc.find("failure") is not None:
        return "failed"
    if tc.find("skipped") is not None:
        return "skipped"
    return "passed"


def _tc_key(tc: ET.Element) -> tuple[str, str]:
    """Return the deduplication key for a testcase (classname, name)."""
    return (tc.get("classname", ""), tc.get("name", ""))


def _find_flaky_keys(suite: ET.Element) -> set[tuple[str, str]]:
    """Identify testcase keys that are flaky within a testsuite.

    A test is flaky when the same (classname, name) pair has both a passing
    and a failing entry in the same testsuite.
    """
    seen: dict[tuple[str, str], set[str]] = {}
    for tc in suite.findall("testcase"):
        key = _tc_key(tc)
        seen.setdefault(key, set()).add(_get_status(tc))

    return {key for key, statuses in seen.items() if "failed" in statuses and "passed" in statuses}


def _update_suite_counts(suite: ET.Element) -> None:
    """Recompute the tests/failures/skipped/errors counts on a testsuite."""
    testcases = suite.findall("testcase")
    total = len(testcases)
    failures = sum(1 for tc in testcases if tc.find("failure") is not None)
    skipped = sum(1 for tc in testcases if tc.find("skipped") is not None)
    errors = sum(1 for tc in testcases if tc.find("error") is not None)

    suite.set("tests", str(total))
    suite.set("failures", str(failures))
    if suite.get("skipped") is not None or skipped > 0:
        suite.set("skipped", str(skipped))
    if suite.get("errors") is not None or errors > 0:
        suite.set("errors", str(errors))


def filter_junit(
    xml_bytes: bytes,
    *,
    status: str | None = None,
    lifecycle: str | None = None,
    no_flaky: bool = False,
) -> bytes:
    """Filter JUnit XML, returning structurally identical XML with fewer testcases.

    Args:
        xml_bytes: Raw JUnit XML content.
        status: Keep only testcases with this status (``failed``, ``passed``,
            or ``skipped``). ``None`` keeps all statuses.
        lifecycle: Keep only testcases with this lifecycle (``blocking`` or
            ``informing``). ``None`` keeps all lifecycles.
        no_flaky: If ``True``, exclude testcases whose (classname, name) key
            is flaky (has both passing and failing entries in the same suite).

    Returns:
        Filtered JUnit XML as bytes.
    """
    root = ET.fromstring(xml_bytes)

    # Handle both <testsuites><testsuite>... and bare <testsuite>...
    if root.tag == "testsuites":
        suites = root.findall("testsuite")
    elif root.tag == "testsuite":
        suites = [root]
    else:
        # Unknown structure — return as-is.
        return xml_bytes

    for suite in suites:
        # Pre-scan for flaky keys within this suite.
        flaky_keys = _find_flaky_keys(suite) if no_flaky else set()

        to_remove: list[ET.Element] = []
        for tc in suite.findall("testcase"):
            remove = False

            if no_flaky and _tc_key(tc) in flaky_keys:
                remove = True

            if status is not None and _get_status(tc) != status:
                remove = True

            if lifecycle is not None and _get_lifecycle(tc) != lifecycle:
                remove = True

            if remove:
                to_remove.append(tc)

        for tc in to_remove:
            suite.remove(tc)

        _update_suite_counts(suite)

    ET.indent(root)
    return ET.tostring(root, encoding="unicode", xml_declaration=True).encode("utf-8")
