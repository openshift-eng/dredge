# dredge — Prow Job Downloader

## Purpose
Downloads artifacts from Prow CI jobs for analysis.

## Project Layout

```
pyproject.toml              - Project metadata, dependencies, and tool config (uv/hatch)
Makefile                    - Dev task runner (lint, format, typecheck, test)
src/dredge/
  __init__.py
  __main__.py               - python -m dredge entry point
  cli.py                    - CLI argument parsing, command dispatch, main()
  commands.py               - Command implementations and shared artifact logic
  py.typed                  - PEP 561 type information marker
  fetcher/                  - HTTP fetching package (encapsulates requests library)
    __init__.py             - Public API: fetch_url(), FetchError, NotFoundError
    _auth.py                - OAuth proxy detection, auth chain follower, Kerberos, cookie cache
    _session.py             - requests.Session singleton, retry logic
  prow/                     - Prow job handling package
    __init__.py             - Public API: import_from_spyglass(), Job, JobImportError, Step
    _metadata.py            - Spyglass URL parsing, gcsweb discovery, step graph/junit parsing
    _step.py                - Step class: get_log(), list_artifacts(), get_artifact(), extract_artifact()
    _gcsweb.py              - gcsweb download and directory listing helpers
    _types.py               - ArtifactEntry, ArtifactType
  junit/                    - JUnit XML analysis package
    __init__.py             - Public API: filter_junit()
    _filter.py              - Filter testcases by status, lifecycle, and flakiness
  discovery/                - Build discovery package
    __init__.py             - Public API: from_prow_history(), from_github_pr(), JobFilter
    _prow_history.py        - Prow job history page scraping, pagination, build filtering
    _github.py              - GitHub API integration (PR job fetching)
    _types.py               - JobFilter enum (FAILED, SUCCESS, ALL)
```

## Architecture

### Data Flow
1. Fetch job history page HTML from Prow (history mode), parse Spyglass URLs directly (import mode), or query GitHub API for PR statuses (pr mode)
2. Extract `var allBuilds = [...]` JSON via regex (history mode only)
3. Filter builds by result (optional: `--failed` / `--no-failed`)
4. For each build:
   - **`import_from_spyglass(spyglass_url, output_dir)` creates the job directory and returns a `Job` (idempotent)**:
     - Parse Spyglass URL → build_id, gcs_path, prow_base_url
     - Discover gcsweb base URL from Spyglass page HTML
     - Fetch and parse ci-operator-step-graph.json → extract top-level steps and inner steps from substeps
     - Classify steps as build, test, or infrastructure
     - Fall back to junit_operator.xml for inner steps when step graph lacks substeps (older jobs)
     - Fetch prowjob.json for job metadata (name, type, PR link)
     - Write `job.json`, `steps.json`, `ci-operator-step-graph.json`
   - **Download build-log.txt and junit XML for failed steps (via `job.failed_steps()` and `Step` methods)**
   - **If --auto-must-gather: discover and download must-gather (scan steps for gather-must-gather substep)**
5. Follow "Older Runs" pagination link if more builds needed (history mode)

### Authentication
The tool automatically detects when a Prow deck requires authentication (via
`_oauth_proxy` cookie in 403 responses) and follows the OAuth redirect chain
using an existing Kerberos ticket. No user interaction required.

- **Detection**: 403 response with `Set-Cookie: _oauth_proxy=`
- **Auth chain**: follows HTTP 3xx redirects and scraped HTML redirects (forms, single-link pages)
- **Kerberos**: SPNEGO authentication against `auth.redhat.com` only (via `gssapi` library)
- **Trust boundary**: only follows redirects to trusted domains (`.openshiftapps.com`, `.openshift.org`, `.redhat.com` by default; extensible via `--trusted-redirect-domain`)
- **Cookie cache**: `~/.config/dredge/cookies/<domain>.json` — cached per-domain, cleared per-domain on expiry
- **Loop detection**: keyed on `(method, url, domain-scoped cookies)` — allows legitimate OAuth revisits where server-side state has changed

The concrete 14-step auth chain is documented in a comment at the top of `src/dredge/fetcher/_auth.py`.

### Key URL Transformations
- SpyglassLink: `/view/gs/BUCKET/PATH` -> GCS path: `BUCKET/PATH`
- Direct download: `https://storage.googleapis.com/{gcs_path}/...`
- Directory listing: `https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/{gcs_path}/...`

### External Dependencies
- Prow job history page structure (var allBuilds JSON)
- GCS bucket public access via storage.googleapis.com
- gcsweb directory listing HTML format
- OpenShift OAuth / Kerberos chain for authenticated Prow instances
- GitHub API for PR status checks (pr mode)

## CLI Usage

### Discovery Subcommands

Discovery subcommands require `-d <dir>` (or `DREDGE_DIR` env var) to specify the output directory.

**import** - Import specific builds by Spyglass URL:
```bash
uv run dredge import -d ./artifacts <url> [<url> ...]

# With automatic must-gather download
uv run dredge import -d ./artifacts --auto-must-gather <url>
```

**history** - Download from job history page:
```bash
# Download most recent N failed jobs (default: --failed)
uv run dredge history -d ./artifacts <url> <count>

# Download all jobs regardless of result
uv run dredge history -d ./artifacts <url> <count> --no-failed

# Download with automatic must-gather
uv run dredge history -d ./artifacts <url> <count> --auto-must-gather
```

**pr** - Download failed prow jobs from a GitHub PR:
```bash
uv run dredge pr -d ./artifacts <github_pr_url>
```

### Discovery Options
- `-d DIR` / `DREDGE_DIR`: Output directory (required)
- `--auto-must-gather`: Automatically download must-gather from steps that contain one

### Step Subcommands

These operate on an existing build directory (previously fetched by a discovery command).
Step names use slash notation for inner steps: `e2e-aws/openshift-e2e-test`.

**step-ls** - List artifacts for a step:
```bash
uv run dredge step-ls -d ./artifacts <build_id> <step_name>
uv run dredge step-ls -d ./artifacts <build_id> <step_name> -p junit
```

**step-log** - Download and print build log for a step:
```bash
uv run dredge step-log -d ./artifacts <build_id> <step_name>
```

**step-get** - Download an artifact from a step:
```bash
uv run dredge step-get -d ./artifacts <build_id> <step_name> -p <artifact_path>

# Download a directory recursively
uv run dredge step-get -d ./artifacts <build_id> <step_name> -p <dir_path> -r
```

**step-extract** - Extract a tar.gz artifact from a step:
```bash
uv run dredge step-extract -d ./artifacts <build_id> <step_name> <tar_path>
```

### JUnit Subcommands

**junit-filter** - Filter JUnit XML by status, lifecycle, and flakiness:
```bash
# Get only blocking failures (most common use case for failure analysis)
uv run dredge junit-filter --status=failed --lifecycle=blocking --no-flaky input.xml

# Get only informing test failures
uv run dredge junit-filter --status=failed --lifecycle=informing input.xml

# Remove flaky tests from results
uv run dredge junit-filter --no-flaky input.xml

# Read from stdin
cat input.xml | uv run dredge junit-filter --status=failed -
```

The output is structurally identical JUnit XML with excluded testcases removed
and testsuite counters updated. Output goes to stdout.

- `--status`: Filter by test status (`failed`, `passed`, `skipped`)
- `--lifecycle`: Filter by lifecycle property (`blocking`, `informing`). Tests without a lifecycle property are treated as blocking.
- `--no-flaky`: Exclude flaky tests. A test is flaky when the same (classname, name) pair has both passing and failing entries in the same testsuite. Flaky tests do not cause job failure.

### Must-gather Subcommands

**fetch-must-gather** - Download and extract must-gather by build ID:
```bash
# Auto-detect which step has the must-gather
uv run dredge fetch-must-gather -d ./artifacts <build_id>

# Specify the step name explicitly
uv run dredge fetch-must-gather -d ./artifacts <build_id> -s e2e-aws
```

### Global Options
- `--trusted-redirect-domain`: Additional trusted domain for auth redirects (may be repeated; prefix with `.` for suffix match)

## Code Quality Requirements

All code changes must pass `make check` (lint + typecheck + test) before they are considered complete.

**Linting and formatting** — Ruff enforces style and catches bugs:
```bash
make lint                   # check for lint errors
make format                 # auto-format code
```
Ruff configuration is in `pyproject.toml` under `[tool.ruff]`. The enabled rule sets are: pycodestyle, pyflakes, isort, pyupgrade, bugbear, simplify, and ruff-specific rules. Line length limit is 100.

**Type checking** — mypy runs in gradual mode:
```bash
make typecheck              # run mypy
```
New code must include type annotations. Existing untyped code in `fetcher/` and `prow/` is grandfathered but should be annotated when modified. mypy configuration is in `pyproject.toml` under `[tool.mypy]`.

**Tests** — pytest with the `responses` library for HTTP mocking:
```bash
make test                   # run test suite
```
Test files live in `tests/`. New functionality should include tests.

**Full check** — run all of the above in sequence:
```bash
make check                  # lint + typecheck + test
```

## Common Modifications

### Adding new artifact types
1. Add download logic in `commands._process_build()`
2. Handle 404 gracefully (some builds may not have the artifact)

### Changing must-gather discovery
`commands._download_must_gather(job)` iterates top-level steps looking for a
`gather-must-gather` substep via `job.step(name, "gather-must-gather")`, then
extracts via `step.extract_artifact("must-gather.tar")`. Must-gather is not
downloaded by default; use `--auto-must-gather` during discovery or the
`fetch-must-gather` subcommand on an existing build directory.

### Pagination
`discovery._prow_history._get_next_page_url()` extracts the "Older Runs" link. The buildId query
parameter references the oldest build on the current page.

### Adding new subcommands
1. Add a new subparser in `cli.parse_args()`
2. Implement `cmd_newmode(...)` in `commands.py`
3. Add dispatch in `cli.main()`

### Incremental Downloads
The `prow.import_from_spyglass()` function is idempotent: if both `job.json` and `steps.json`
exist in the build directory, it returns a `Job` immediately without fetching.
Individual artifact downloads are also idempotent:
- `step.get_log()`: Skipped if `build-log.txt` already exists on disk
- `step.get_artifact(path)`: Skipped if the artifact file already exists on disk
- `step.extract_artifact(path)`: Skipped if extracted directory exists and is non-empty
- `must-gather/`: Skipped if directory exists (both --auto-must-gather and fetch-must-gather command)

To force re-import, delete `job.json` or `steps.json` from the build directory.

### Build Metadata
Each build directory contains `job.json` and `steps.json`, loaded by `Job(job_dir)`.
`job.json` fields (also available as `Job` attributes):
- `spyglass`: Link to the Prow job page (Spyglass view)
- `build_id`: The Prow build ID
- `job_name`: CI job name
- `job_type`: Job type (presubmit, postsubmit, periodic)
- `pr_link`: GitHub PR URL (if PR job, null otherwise)
- `gcs_path`: GCS bucket path for this build's artifacts
- `gcsweb_base`: Base URL for gcsweb artifact access
`steps.json` contains a recursive step hierarchy keyed by step name.
Each entry has `status` (passed/failed/skipped), `type` (build/test/infrastructure).
Multi-stage tests also have `substeps: { inner_step_name: { status: ... }, ... }`.
Use `job.step(name)` or `job.step(name, inner_name)` to get `Step` objects.
Use `job.failed_steps()` to get all failed inner steps as `Step` objects.
Build steps share the top-level ci-operator build-log.txt (symlinked into the step dir).

### Step Classification
Steps are classified by `_metadata._classify_step()`:
- **build**: Has Build manifests, or description starts with "Build image " / "Store build results " / "Clone the correct source"
- **test**: Has substeps, or description starts with "Run multi-stage test " / "Run test "
- **infrastructure**: Everything else

Build steps have no artifact directory; their log is the shared ci-operator build-log.txt.

## Error Handling

| Scenario | Action |
|----------|--------|
| Network error | Retry 3x with backoff, then fail |
| allBuilds not in HTML | Exit with error (page structure changed) |
| junit_operator.xml 404 | JobImportError (only when step graph has no substeps) |
| junit_operator.xml unparseable | JobImportError |
| build-log.txt 404 | FetchError caught, continue to next step |
| must-gather 404 | ArtifactError, logged as warning (auto) or error (command) |
| Extraction fails | ArtifactError raised |
| Invalid URL in import mode | Error log, skip that URL |
| Auth required, no gssapi | Error: install with `uv sync --extra kerberos` |
| Auth required, no Kerberos ticket | Error: run `kinit` and retry |
| Auth redirect to untrusted domain | Error: use `--trusted-redirect-domain` |
| Auth redirect loop | Error with URL (keyed on method+url+cookies) |
| Cached cookie expired | Clear cache for that domain, re-authenticate |
| Build step artifact access | BuildStepArtifactError (build steps have no artifact dir) |

## Testing
```bash
# Verify help text
uv run dredge --help

# Test import mode
uv run dredge import -d ./artifacts \
    "https://prow.ci.openshift.org/view/gs/origin-ci-test/logs/periodic-ci-openshift-release-master-ci-4.22-e2e-azure-ovn-upgrade/2016123606924267520"

# Test history mode - failures only (default)
uv run dredge history -d ./artifacts \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    2

# Test history mode - all results
uv run dredge history -d ./artifacts \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    5 --no-failed

# With auto must-gather
uv run dredge history -d ./artifacts --auto-must-gather \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    5

# Step commands on existing build
uv run dredge step-ls -d ./artifacts <build_id> e2e-aws/openshift-e2e-test
uv run dredge step-log -d ./artifacts <build_id> e2e-aws/openshift-e2e-test
uv run dredge step-get -d ./artifacts <build_id> e2e-aws/openshift-e2e-test -p junit/test.xml
uv run dredge step-extract -d ./artifacts <build_id> e2e-aws/gather-must-gather must-gather.tar

# Authenticated Prow deck (requires kinit + gssapi)
uv run dredge import -d ./artifacts \
    "https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com/view/gs/qe-private-deck/..."
```

## Prow Job Artifact Structure References

The artifacts this tool downloads are produced by `ci-operator` in [openshift/ci-tools](https://github.com/openshift/ci-tools). Key source files:

- **`junit_operator.xml` generation**: `cmd/ci-operator/main.go` — `writeJUnit(suites, "operator")` saves ci-operator's own execution report. Each multi-stage test step becomes a `<testcase>` named `"Run multi-stage test {test} - {test}-{step} container test"`. Failure output is the step's error string, which includes the kubelet's truncated container termination message (not the full log).

- **Step execution and error construction**: `pkg/steps/run.go` — `Run()` executes the step graph as a DAG, creating one junit test case per step from `err.Error()`. `pkg/steps/multi_stage/run.go` — `runPod()` constructs the error for failed multi-stage steps, wrapping the result from `pkg/util/pods.go` `WaitForPodCompletion()` → `processPodEvent()`.

- **Step graph JSON**: `pkg/api/graph.go` — `CIOperatorStepDetails` / `CIOperatorStepDetailInfo` define the schema for `ci-operator-step-graph.json`. Includes `name`, `failed`, `started_at`, `finished_at`, `duration`, `dependencies`, `substeps`. The `substeps` field is populated for multi-stage test steps since ci-tools PR #5151.

- **Artifact directory layout**: Multi-stage test artifacts land under `artifacts/{test_name}/{inner_step}/`. The inner step directory name is the step name with the test name prefix stripped (e.g. step `e2e-aws-ovn-openshift-e2e-test` → directory `openshift-e2e-test`). Each step directory contains `build-log.txt` (complete container log) and an `artifacts/` subdirectory with step-produced files (junit XML, etc.).

- **Per-container sub-tests**: `pkg/steps/artifacts.go` — `TestCaseNotifier.SubTests()` generates per-container junit entries from the `ci-operator.openshift.io/container-sub-tests` pod annotation. Failure output is `state.Terminated.Message`, subject to kubelet's 4KB termination message limit.

## Dependencies
- Python 3.11+
- `requests` library (managed via pyproject.toml / uv)
- `gssapi` (optional, for Kerberos auth): `uv sync --extra kerberos`

### Dev Dependencies
Installed via `uv sync --dev`:
- `pytest` + `responses` — testing
- `ruff` — linting and formatting
- `mypy` + `types-requests` — type checking
