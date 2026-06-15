import xml.etree.ElementTree as ET

from dredge.junit import filter_junit

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BLOCKING_TC = """\
<testcase name="blocking-test" classname="suite" time="1.0">
  <properties>
    <property name="lifecycle" value="blocking"/>
  </properties>
</testcase>"""

INFORMING_TC = """\
<testcase name="informing-test" classname="suite" time="1.0">
  <properties>
    <property name="lifecycle" value="informing"/>
  </properties>
</testcase>"""

NO_LIFECYCLE_TC = """\
<testcase name="no-lifecycle-test" classname="suite" time="1.0">
</testcase>"""

FAILED_BLOCKING_TC = """\
<testcase name="failed-blocking" classname="suite" time="1.0">
  <properties>
    <property name="lifecycle" value="blocking"/>
  </properties>
  <failure message="boom"/>
</testcase>"""

FAILED_INFORMING_TC = """\
<testcase name="failed-informing" classname="suite" time="1.0">
  <properties>
    <property name="lifecycle" value="informing"/>
  </properties>
  <failure message="boom"/>
</testcase>"""

SKIPPED_TC = """\
<testcase name="skipped-test" classname="suite" time="0">
  <skipped message="not applicable"/>
</testcase>"""


def _wrap_suite(*testcases: str) -> bytes:
    inner = "\n".join(testcases)
    return (
        f'<testsuites><testsuite name="s" tests="{len(testcases)}">'
        f"{inner}</testsuite></testsuites>"
    ).encode()


def _parse(xml_bytes: bytes) -> ET.Element:
    return ET.fromstring(xml_bytes)


def _tc_names(xml_bytes: bytes) -> list[str]:
    root = _parse(xml_bytes)
    return [tc.get("name", "") for tc in root.iter("testcase")]


def _suite_counts(xml_bytes: bytes) -> dict[str, int]:
    root = _parse(xml_bytes)
    suite = root.find("testsuite") if root.tag == "testsuites" else root
    assert suite is not None
    return {
        "tests": int(suite.get("tests", "0")),
        "failures": int(suite.get("failures", "0")),
    }


# ---------------------------------------------------------------------------
# Lifecycle filtering
# ---------------------------------------------------------------------------


class TestLifecycleFilter:
    def test_blocking_keeps_blocking_and_no_lifecycle(self):
        xml = _wrap_suite(BLOCKING_TC, INFORMING_TC, NO_LIFECYCLE_TC)
        result = filter_junit(xml, lifecycle="blocking")
        names = _tc_names(result)
        assert "blocking-test" in names
        assert "no-lifecycle-test" in names
        assert "informing-test" not in names

    def test_informing_keeps_only_informing(self):
        xml = _wrap_suite(BLOCKING_TC, INFORMING_TC, NO_LIFECYCLE_TC)
        result = filter_junit(xml, lifecycle="informing")
        names = _tc_names(result)
        assert names == ["informing-test"]

    def test_no_filter_keeps_all(self):
        xml = _wrap_suite(BLOCKING_TC, INFORMING_TC, NO_LIFECYCLE_TC)
        result = filter_junit(xml)
        assert len(_tc_names(result)) == 3


# ---------------------------------------------------------------------------
# Status filtering
# ---------------------------------------------------------------------------


class TestStatusFilter:
    def test_failed_only(self):
        xml = _wrap_suite(BLOCKING_TC, FAILED_BLOCKING_TC, SKIPPED_TC)
        result = filter_junit(xml, status="failed")
        names = _tc_names(result)
        assert names == ["failed-blocking"]

    def test_passed_only(self):
        xml = _wrap_suite(BLOCKING_TC, FAILED_BLOCKING_TC, SKIPPED_TC)
        result = filter_junit(xml, status="passed")
        names = _tc_names(result)
        assert names == ["blocking-test"]

    def test_skipped_only(self):
        xml = _wrap_suite(BLOCKING_TC, FAILED_BLOCKING_TC, SKIPPED_TC)
        result = filter_junit(xml, status="skipped")
        names = _tc_names(result)
        assert names == ["skipped-test"]


# ---------------------------------------------------------------------------
# Combined filters
# ---------------------------------------------------------------------------


class TestCombinedFilters:
    def test_failed_blocking(self):
        xml = _wrap_suite(
            BLOCKING_TC, FAILED_BLOCKING_TC, FAILED_INFORMING_TC, INFORMING_TC
        )
        result = filter_junit(xml, status="failed", lifecycle="blocking")
        names = _tc_names(result)
        assert names == ["failed-blocking"]

    def test_failed_informing(self):
        xml = _wrap_suite(
            BLOCKING_TC, FAILED_BLOCKING_TC, FAILED_INFORMING_TC, INFORMING_TC
        )
        result = filter_junit(xml, status="failed", lifecycle="informing")
        names = _tc_names(result)
        assert names == ["failed-informing"]


# ---------------------------------------------------------------------------
# Flaky filtering
# ---------------------------------------------------------------------------


FLAKY_PASS = """\
<testcase name="flaky-test" classname="suite" time="1.0">
  <properties>
    <property name="lifecycle" value="blocking"/>
  </properties>
</testcase>"""

FLAKY_FAIL = """\
<testcase name="flaky-test" classname="suite" time="1.0">
  <properties>
    <property name="lifecycle" value="blocking"/>
  </properties>
  <failure message="boom"/>
</testcase>"""


class TestFlakyFilter:
    def test_no_flaky_removes_all_entries_for_flaky_test(self):
        xml = _wrap_suite(FLAKY_PASS, FLAKY_FAIL, FAILED_BLOCKING_TC)
        result = filter_junit(xml, no_flaky=True)
        names = _tc_names(result)
        assert "flaky-test" not in names
        assert "failed-blocking" in names

    def test_no_flaky_with_status_filter(self):
        xml = _wrap_suite(FLAKY_PASS, FLAKY_FAIL, FAILED_BLOCKING_TC)
        result = filter_junit(xml, status="failed", no_flaky=True)
        names = _tc_names(result)
        assert "flaky-test" not in names
        assert "failed-blocking" in names

    def test_without_no_flaky_keeps_flaky(self):
        xml = _wrap_suite(FLAKY_PASS, FLAKY_FAIL, FAILED_BLOCKING_TC)
        result = filter_junit(xml, status="failed")
        names = _tc_names(result)
        assert "flaky-test" in names

    def test_flaky_detection_requires_mixed_results(self):
        """Two failing entries with the same name are NOT flaky."""
        fail1 = FLAKY_FAIL
        fail2 = FLAKY_FAIL
        xml = _wrap_suite(fail1, fail2)
        result = filter_junit(xml, no_flaky=True)
        names = _tc_names(result)
        assert "flaky-test" in names


# ---------------------------------------------------------------------------
# Suite counter updates
# ---------------------------------------------------------------------------


class TestSuiteCounters:
    def test_counters_updated_after_filter(self):
        xml = _wrap_suite(BLOCKING_TC, FAILED_BLOCKING_TC, FAILED_INFORMING_TC)
        result = filter_junit(xml, status="failed")
        counts = _suite_counts(result)
        assert counts["tests"] == 2
        assert counts["failures"] == 2

    def test_empty_result_has_zero_counts(self):
        xml = _wrap_suite(BLOCKING_TC)
        result = filter_junit(xml, status="failed")
        counts = _suite_counts(result)
        assert counts["tests"] == 0
        assert counts["failures"] == 0


# ---------------------------------------------------------------------------
# Structural preservation
# ---------------------------------------------------------------------------


class TestStructuralPreservation:
    def test_output_is_valid_xml(self):
        xml = _wrap_suite(BLOCKING_TC, FAILED_BLOCKING_TC)
        result = filter_junit(xml, status="failed")
        # Should parse without error
        root = ET.fromstring(result)
        assert root.tag == "testsuites"

    def test_preserves_testsuite_name(self):
        xml = _wrap_suite(BLOCKING_TC)
        result = filter_junit(xml)
        root = ET.fromstring(result)
        suite = root.find("testsuite")
        assert suite is not None
        assert suite.get("name") == "s"

    def test_preserves_testcase_properties(self):
        xml = _wrap_suite(FAILED_BLOCKING_TC)
        result = filter_junit(xml, status="failed")
        root = ET.fromstring(result)
        tc = root.find(".//testcase")
        assert tc is not None
        props = tc.find("properties")
        assert props is not None
        prop = props.find("property[@name='lifecycle']")
        assert prop is not None
        assert prop.get("value") == "blocking"

    def test_preserves_failure_element(self):
        xml = _wrap_suite(FAILED_BLOCKING_TC)
        result = filter_junit(xml, status="failed")
        root = ET.fromstring(result)
        tc = root.find(".//testcase")
        assert tc is not None
        fail = tc.find("failure")
        assert fail is not None
        assert fail.get("message") == "boom"

    def test_bare_testsuite_root(self):
        """Handle JUnit files that use <testsuite> as the root element."""
        xml = (
            b'<testsuite name="s" tests="2">'
            b'<testcase name="a"><failure/></testcase>'
            b'<testcase name="b"/>'
            b"</testsuite>"
        )
        result = filter_junit(xml, status="failed")
        root = ET.fromstring(result)
        assert root.tag == "testsuite"
        names = [tc.get("name") for tc in root.findall("testcase")]
        assert names == ["a"]
