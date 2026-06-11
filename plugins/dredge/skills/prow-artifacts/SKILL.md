---
name: prow-artifacts
description: Download Prow CI job artifacts for analysis. Understands Prow artifact structure, so is more efficient than manual fetching. Use when user provides a prow URL, asks about CI job results (pass or fail), mentions a PR's test results (pass or fail), or wants to examine CI logs.
---

# Dredge

Download Prow CI job artifacts with `dredge` and make them available for analysis.

## Quick start

1. Identify the mode from user input
2. Run `dredge` with `-d "$(pwd)/.dredge"` to download
3. Read `.dredge/<build_id>/steps.json` to see what ran

## Resolving the target

The user may not provide a URL directly. If they reference "this PR", "my PR", a PR number, or CI failures on the current branch, resolve it to a GitHub PR URL first:

```bash
# From a branch name
gh pr list --head <branch> --json url --jq '.[0].url'

# From a PR number
gh pr view <number> --json url --jq '.url'

# Current branch
gh pr view --json url --jq '.url'
```

Then proceed with the resolved URL using `dredge pr`.

## Determine mode

| User input | Command |
|---|---|
| GitHub PR URL (`github.com/org/repo/pull/N`) | `dredge pr` |
| Prow Spyglass URL(s) containing `/view/gs/` | `dredge import` |
| Prow job-history URL containing `/job-history/` | `dredge history` |

## Working directory

Always use `.dredge` in the current working directory. Pass as an absolute path via `-d`.

## Download commands

```bash
# PR — downloads all failed jobs for the PR
dredge -d "$(pwd)/.dredge" pr <github_pr_url>

# Specific Spyglass URLs
dredge -d "$(pwd)/.dredge" import <url> [<url> ...]

# Job history — most recent N failures
dredge -d "$(pwd)/.dredge" history <job_history_url> <count>

# To also download must-gather artifacts during import, add --auto-must-gather
dredge -d "$(pwd)/.dredge" pr --auto-must-gather <github_pr_url>
```

## After import

Each build creates a directory `.dredge/<build_id>/` containing:

- **`job.json`** — build metadata: spyglass link, build ID, job name, PR link, GCS path
- **`steps.json`** — hierarchical step structure with status and type for each step

**Always read `steps.json` first.** It shows every step that ran, its status (`passed`, `failed`, or `skipped`), its type (`build` or `test`), and for multi-phase tests, the substeps nested under a parent step. Example structure:

> **Note:** `dredge pr` only downloads artifacts from **failed** steps. If you need artifacts from passed steps (e.g., test logs when a validation step failed), use the step-specific commands documented below.

```json
{
  "src": { "status": "passed", "type": "build" },
  "azure-cloud-controller-manager": { "status": "failed", "type": "build" },
  "e2e-azure-ovn-upgrade": {
    "status": "skipped",
    "type": "test",
    "substeps": {
      "setup": { "status": "skipped" },
      "test": { "status": "skipped" }
    }
  }
}
```

### Step types

- **`build`** — image builds (e.g. `src`, `bin`, named images like `azure-cloud-controller-manager`). Build failures indicate compilation or Dockerfile errors. Build step logs are the top-level ci-operator log, not a per-step log. Build steps have no artifacts directory.
- **`test`** — multi-stage tests and container tests. These have per-step logs and artifacts.

### Step status

- **`passed`** — step executed successfully
- **`failed`** — step executed and failed
- **`skipped`** — step never ran (a dependency failed before it could start)

### Step directories

Each downloaded step has a directory at `.dredge/<build_id>/<parent_step>/<step_name>/` containing a `build-log.txt` with the direct output of that step.

- **Build steps** (e.g. `src`, `bin`, named images): the log is a symlink to the top-level ci-operator log. Search for the step name in this log to find relevant build output. Build steps have no `artifacts/` directory.
- **Test steps** (e.g. `test`): the log usually contains everything you need — test output, failure messages, stack traces.
- **Artifact-gathering steps** (e.g. `gather-must-gather`, `gather-extra`): the log is usually not interesting. The value is in the artifacts, located under `artifacts/` within the step directory.

## Downloading artifacts from specific steps

After importing, you can download artifacts from ANY step (passed or failed):

```bash
# List available artifacts for a step
dredge -d "$(pwd)/.dredge" step-ls <build_id> <step_path>

# Download build log for a step
dredge -d "$(pwd)/.dredge" step-log <build_id> <step_path>

# Download a specific artifact
dredge -d "$(pwd)/.dredge" step-get <build_id> <step_path> -p <artifact_path>

# Download an entire directory recursively
dredge -d "$(pwd)/.dredge" step-get <build_id> <step_path> -p <artifact_dir> -r
```

Example: Download test logs from a passed test step:
```bash
# List what's available
dredge -d "$(pwd)/.dredge" step-ls 2056305481017724928 regression-clusterinfra-azure-ipi-mapi/openshift-extended-test

# Get the test log
dredge -d "$(pwd)/.dredge" step-get 2056305481017724928 regression-clusterinfra-azure-ipi-mapi/openshift-extended-test -p artifacts/extended.log
```

### Downloading must-gather separately

If must-gather was not downloaded during import, fetch it explicitly:

```bash
dredge -d "$(pwd)/.dredge" fetch-must-gather <build_id>
```

## JUnit test results

Root-level JUnit XML files (`junit_operator.xml`, `prowjob_junit.xml`) are automatically downloaded during import. Step-level JUnit files are downloaded for failing steps.

### Blocking, informing, and flaky

Prow classifies test results into three categories. **Blocking failures are the ones that cause the job to fail** — always start a failure analysis here.

- **Blocking** — Tests with `<property name="lifecycle" value="blocking"/>` or no lifecycle property. If any blocking test fails, the job fails. These are the most important in a failure analysis.
- **Informing** — Tests with `<property name="lifecycle" value="informing"/>`. These do **not** cause the job to fail even if they fail. They provide signal about features in development or known issues.
- **Flaky** — When a test name appears more than once within a single JUnit XML file (one entry with `<failure>`, one without), Spyglass counts it as flaky rather than failed. This is common in `e2e-monitor-tests` XML files.

### Key JUnit files

- **`junit_e2e__*.xml`** — Individual e2e test results (pass/fail for each `[sig-*]` test). This is the primary file for identifying which e2e tests failed.
- **`e2e-monitor-tests__*.xml`** — Monitor and invariant test results. These catch cluster-level problems like pathologically repeating events, alert firing, and disruption. **Failures use a different format than standard e2e tests** — they appear as `<testcase>` entries with `<failure>` elements, not in build-log grep output.
- **`junit_operator.xml`** — ci-operator's record of step-level pass/fail. Redundant with `steps.json` but included for completeness.
- **`junit_e2e_analysis__*.xml`** — Post-test cluster health checks produced by `gather-extra`: machine state, node readiness, operator conditions. Only interesting if they fail.
- **`junit_symptoms.xml`** — Symptom detectors produced by `gather-extra`: panic detection, segfaults, quota exhaustion. Only interesting if they fail.

## Known substeps

| Substep | Type | Description | Guide |
|---|---|---|---|
| `gather-must-gather` | Artifact collection | OpenShift cluster diagnostic snapshot: node state, resource dumps, etcd health, operator logs, networking diagnostics | [gather-must-gather](references/gather-must-gather.md) |
| `gather-extra` | Artifact collection | Post-test cluster analysis: produces `junit_e2e_analysis` and `junit_symptoms` XML files with cluster health checks |
