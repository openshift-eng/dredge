# dredge

A CLI tool for downloading artifacts from OpenShift [Prow](https://docs.prow.k8s.io/) CI jobs.

Dredge automates the process of navigating Prow's web UI and GCS buckets to retrieve build artifacts. It downloads JUnit results, build logs for failed steps, must-gather diagnostics, and HyperShift hosted cluster dumps, then organizes them locally for analysis.

## Installation

Requires Python 3.10+. Install with [uv](https://docs.astral.sh/uv/):

```sh
uv sync
```

For authenticated Prow decks (e.g. Red Hat internal), install with Kerberos support:

```sh
uv sync --extra kerberos
```

## Usage

### Download builds from a job history page

```sh
dredge history -d ./artifacts <job-history-url> <count>
```

Fetches the most recent `<count>` builds from a Prow job history page. Filter by result with `--failure` or `--success`.

```sh
dredge history -d ./artifacts \
  "https://prow.ci.openshift.org/job-history/gs/origin-ci-test/logs/e2e-aws-ovn" \
  10 --failure
```

### Download specific builds by URL

```sh
dredge urls -d ./artifacts <spyglass-url> [<spyglass-url> ...]
```

Process one or more Prow Spyglass build URLs directly.

### Download failed jobs from a GitHub PR

```sh
dredge pr -d ./artifacts <github-pr-url>
```

Discovers failed Prow jobs from the PR's commit statuses and downloads their artifacts. Uses the `gh` CLI for authentication if available, falling back to unauthenticated requests.

### Download must-gather from an existing build

```sh
dredge must-gather ./artifacts/<build-id> [step-name]
```

Downloads OpenShift must-gather diagnostics from a previously downloaded build. The step name is auto-detected if omitted.

### Download HyperShift cluster dumps

```sh
dredge hypershift-dump ./artifacts/<build-id> [step-name]
```

Downloads `hostedcluster.tar` archives from HyperShift test steps.

### Automatic artifact downloads

Pass `--auto` (or `--auto-must-gather` / `--auto-hypershift` individually) to any discovery command to automatically download must-gather and HyperShift dumps alongside the standard artifacts:

```sh
dredge history -d ./artifacts <url> 5 --failure --auto
```

## What gets downloaded

Each build is saved to `<output-dir>/<build-id>/` with:

| File | Description |
|------|-------------|
| `build_info.json` | Build metadata: ID, timestamps, URLs, step hierarchy with pass/fail status |
| `junit_operator.xml` | JUnit test results from ci-operator |
| `ci-operator-step-graph.json` | Step graph with timing, dependencies, and status |
| `build-logs/<step>/` | Build logs and JUnit XML for failed steps |
| `must-gather/` | Extracted OpenShift cluster diagnostics (if requested) |
| `hypershift-dumps/` | Extracted HyperShift hosted cluster dumps (if requested) |

## Authentication

Dredge handles OAuth and Kerberos authentication automatically for protected Prow decks. It follows redirect chains, performs SPNEGO negotiation when needed, and caches cookies in `~/.config/dredge/cookies/`.

For Kerberos-authenticated decks, obtain a ticket first:

```sh
kinit user@IPA.REDHAT.COM
dredge urls -d ./artifacts <protected-url>
```

To allow redirects to additional domains during authentication:

```sh
dredge --trusted-redirect-domain .example.com urls -d ./artifacts <url>
```

## Running without installing

```sh
uv run dredge <command> [options]
```