A prow job is represented canonically by its prow build-id
A prow job must first be imported from a spyglass URL
A prow job is conceptually a list of executed steps, their artifacts and whether they were successful

* discover
- Obtain a list of prow jobs represented as spyglass URLs from a source
- PR (all|failed)
- History (prow history url)

* fetch_url <spyglass or gcsweb URL>
- Returns raw contents of a URL
- Authenticates if necessary
- Returns some python interface that can be efficiently streamed from the underlying library (e.g. requests) and consumed flexibly by caller (e.g. write to file, read into a string)

* import_job <spyglass URL> -> local path of job directory
- Creates a local job directory, named by build-id
- Obtains list of steps as a flat list e.g
  - aws-ipi-disc-priv-f28/aws-deprovision-s3buckets
  - aws-ipi-disc-priv-f28/aws-deprovision-security-group
  - aws-ipi-disc-priv-f28/aws-deprovision-stack
- Each step has the following metadata:
  - success: <boolean>
- steps are written to <job directory>/steps.json
- job.json contains:
  - spyglass: <spyglass url>

* step_get_log <build-id> <step> -> local path of downloaded artifact
- Download build-log.

* step_list_artifacts <build-id> <step> <path>
- Returns a list of directory entries for the step at <path>
- Top level path is "."
- Directory entry is:
  * filename
  * size: <size in bytes> (unset for directories)
  * type: (dir|file)

* step_get_artifact <build-id> <step> <path> -> local path of downloaded artifact

Layout of job directory:
<build-id>/
  job.json
  steps.json
  <step/...>
    build-log.txt
    artifacts/
      ...

* Needs authentication:
https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com/view/gs/qe-private-deck/pr-logs/pull/openshift_release/77955/rehearse-77955-periodic-ci-openshift-openshift-tests-private-release-4.19-amd64-nightly-4.19-upgrade-from-stable-4.18-aws-ipi-disc-priv-f28/2045122020844244992

* No authentication required:
https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/openshift_ci-tools/5151/pull-ci-openshift-ci-tools-main-breaking-changes/2050218808576053248

* e2e/e2e step contains artifacts with directories:
https://prow.ci.openshift.org/view/gs/test-platform-results/pr-logs/pull/openshift_ci-tools/5151/pull-ci-openshift-ci-tools-main-e2e/2050267583197745152