# dredge

A CLI tool for downloading artifacts from OpenShift [Prow](https://docs.prow.k8s.io/) CI jobs.

Dredge automates the process of navigating Prow's web UI and GCS buckets to retrieve build artifacts. It downloads build logs, step metadata, and must-gather diagnostics, then organizes them locally for analysis.

## Installation

Requires Python 3.10+. Install with [uv](https://docs.astral.sh/uv/).

The repo is private, so configure git authentication first:

```sh
gh auth setup-git
```

Then install dredge as a CLI tool:

```sh
uv tool install git+https://github.com/openshift-cloud-team/dredge
```

For authenticated Prow decks (e.g. Red Hat internal), install with Kerberos support:

```sh
uv tool install --with "dredge[kerberos]" git+https://github.com/openshift-cloud-team/dredge
```

### Development

To work on dredge locally, clone the repo and install dependencies:

```sh
uv sync
```

## Usage

All discovery commands (`import`, `pr`, `history`) accept `-d DIR` to set the output directory.

### Download failed jobs from a GitHub PR

```sh
dredge -d .dredge pr <github-pr-url>
```

Discovers failed Prow jobs from the PR's commit statuses and downloads their artifacts. Uses the `gh` CLI for authentication if available, falling back to unauthenticated requests.

### Download specific builds by URL

```sh
dredge -d .dredge import <spyglass-url> [<spyglass-url> ...]
```

Import one or more Prow Spyglass build URLs directly.

### Download builds from a job history page

```sh
dredge -d .dredge history <job-history-url> <count>
```

Fetches the most recent `<count>` builds from a Prow job history page. Only failed jobs are downloaded by default; use `--no-failed` to include all results.

```sh
dredge -d .dredge history \
  "https://prow.ci.openshift.org/job-history/gs/origin-ci-test/logs/e2e-aws-ovn" \
  10
```

### Download must-gather

Download must-gather diagnostics from a build already in the dredge directory:

```sh
dredge -d .dredge fetch-must-gather <build-id>
```

The step name is auto-detected. To specify it explicitly:

```sh
dredge -d .dredge fetch-must-gather -s <step-name> <build-id>
```

To automatically download must-gather during import, add `--auto-must-gather` to any discovery command:

```sh
dredge -d .dredge pr --auto-must-gather <github-pr-url>
```

### Step-level commands

Inspect and download individual step artifacts from a build:

```sh
# List artifacts for a step
dredge -d .dredge step-ls <build-id> <step-path>

# Download the build log for a step
dredge -d .dredge step-log <build-id> <step-path>

# Download a specific artifact
dredge -d .dredge step-get -p <artifact-path> <build-id> <step-path>

# Extract a tar.gz artifact
dredge -d .dredge step-extract <build-id> <step-path> <artifact-path>
```

### Filter JUnit XML

Filter JUnit XML files to extract only the test results you care about:

```sh
# Get only the blocking, non-flaky failures that caused the job to fail
dredge junit-filter --status=failed --lifecycle=blocking --no-flaky <junit-file>

# Get only informing test failures
dredge junit-filter --status=failed --lifecycle=informing <junit-file>

# Remove flaky tests from results
dredge junit-filter --no-flaky <junit-file>

# Read from stdin
cat junit.xml | dredge junit-filter --status=failed -
```

The output is structurally identical JUnit XML with excluded testcases removed and suite counters updated.

- `--status` — Filter by test result: `failed`, `passed`, or `skipped`
- `--lifecycle` — Filter by [lifecycle property](https://docs.google.com/document/d/1CI5hAB3bLSqpwl0k23xD9NZbj0PP2oUj0DkRvX7Os4k): `blocking` (default when absent) or `informing`
- `--no-flaky` — Exclude flaky tests (same test name with both pass and fail entries). Flaky tests do not cause job failure.

## What gets downloaded

Each build is saved to `<output-dir>/<build-id>/` with:

| File | Description |
|------|-------------|
| `job.json` | Build metadata: ID, job name, Spyglass link, PR link, GCS path |
| `steps.json` | Step hierarchy with pass/fail status and substeps |
| `ci-operator-step-graph.json` | Raw step graph with timing, dependencies, and status |
| `<parent-step>/<step>/build-log.txt` | Build log for a downloaded step |
| `<parent-step>/<step>/artifacts/` | Artifacts for a downloaded step (e.g. must-gather) |

## Authentication

Dredge handles OAuth and Kerberos authentication automatically for protected Prow decks. It follows redirect chains, performs SPNEGO negotiation when needed, and caches cookies in `~/.config/dredge/cookies/`.

For Kerberos-authenticated decks, obtain a ticket first:

```sh
kinit user@IPA.REDHAT.COM
dredge -d .dredge import <protected-url>
```

To allow redirects to additional domains during authentication:

```sh
dredge --trusted-redirect-domain .example.com -d .dredge import <url>
```

## Claude Code plugin

Dredge includes a [Claude Code](https://claude.ai/code) plugin that teaches Claude how to download and analyze Prow CI job logs. Install it from the dredge marketplace:

```
/plugin marketplace add openshift-cloud-team/dredge
/plugin install dredge@dredge-plugins
```

To update the plugin after the repo is updated:

```
/plugin marketplace update dredge-plugins
```

Once installed, use `/dredge:prow-artifacts` in any project to download Prow job logs by providing a GitHub PR URL, Prow Spyglass URL, or job history URL.

## Running without installing

```sh
uv run dredge <command> [options]
```
