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
  cli.py                    - CLI argument parsing, command handlers, main()
  py.typed                  - PEP 561 type information marker
  fetcher/                  - HTTP fetching package (encapsulates requests library)
    __init__.py             - Public API: fetch_url(), FetchError, NotFoundError
    _auth.py                - OAuth proxy detection, auth chain follower, Kerberos, cookie cache
    _session.py             - requests.Session singleton, retry logic
  prow/                     - Prow job handling package
    __init__.py             - Public API: import_from_spyglass(), Job, JobImportError, Step
    _metadata.py            - Spyglass URL parsing, gcsweb discovery, step graph/junit parsing
    _step.py                - Step class: get_log(), list_artifacts(), get_artifact()
    _gcsweb.py              - gcsweb download and directory listing helpers
  discovery.py              - Prow URL handling, build discovery, pagination
  artifacts.py              - Artifact download, extraction, downstream operations
  github.py                 - GitHub API integration (PR job fetching)
```

## Architecture

### Data Flow
1. Fetch job history page HTML from Prow (history mode) or parse Spyglass URLs directly (urls mode)
2. Extract `var allBuilds = [...]` JSON via regex (history mode only)
3. Filter builds by result (optional: --failure, --success, or both)
4. For each build:
   - **`import_from_spyglass(spyglass_url, output_dir)` creates the job directory and returns a `Job` (idempotent)**:
     - Parse Spyglass URL → build_id, gcs_path, prow_base_url
     - Discover gcsweb base URL from Spyglass page HTML
     - Fetch and parse ci-operator-step-graph.json → extract job metadata and top-level steps
     - Fetch and parse junit_operator.xml → extract inner steps for multi-stage tests
     - Write `job.json`, `steps.json`, `ci-operator-step-graph.json`
   - **Download build-log.txt and junit XML for failed steps (via `job.failed_steps()` and `Step` methods)**
   - **If --auto-must-gather: discover and download must-gather (scan steps for gather-must-gather substep)**
   - **If --auto-hypershift: discover and download hypershift dumps (read step graph for dependencies)**
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

## CLI Usage

### Discovery Subcommands

All discovery subcommands require `-d <dir>` to specify the output directory.

**history** - Download from job history page:
```bash
# Download most recent N jobs (any result)
uv run dredge history -d ./artifacts <url> <count>

# Download only failed jobs
uv run dredge history -d ./artifacts <url> <count> --failure

# Download only successful jobs
uv run dredge history -d ./artifacts <url> <count> --success

# Download with automatic must-gather and hypershift dump downloads
uv run dredge history -d ./artifacts <url> <count> --failure --auto
```

**urls** - Download specific builds by Spyglass URL:
```bash
uv run dredge urls -d ./artifacts <url> [<url> ...]
```

**pr** - Download failed prow jobs from a GitHub PR:
```bash
uv run dredge pr -d ./artifacts <github_pr_url>
```

### Discovery Options
- `-d DIR`: Output directory (required)
- `--auto-must-gather`: Automatically download must-gather from steps that contain one
- `--auto-hypershift`: Automatically download hypershift hosted cluster dumps
- `--auto`: Enable all automatic artifact downloads (equivalent to --auto-must-gather --auto-hypershift)

### Standalone Artifact Subcommands

These operate on an existing build directory (previously fetched by a discovery command).

**must-gather** - Download must-gather from a build directory:
```bash
# Auto-detect which step has the must-gather
uv run dredge must-gather ./artifacts/<build_id>

# Specify the step name explicitly
uv run dredge must-gather ./artifacts/<build_id> e2e-aws-ovn
```

**hypershift-dump** - Download hypershift dumps from a build directory:
```bash
# Auto-detect hypershift test steps
uv run dredge hypershift-dump ./artifacts/<build_id>

# Specify the step name explicitly
uv run dredge hypershift-dump ./artifacts/<build_id> e2e-hypershift
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
1. Add download logic in `artifacts.process_build()`
2. Handle 404 gracefully (some builds may not have the artifact)

### Changing must-gather discovery
`artifacts.download_must_gather(job)` iterates top-level steps looking for a
`gather-must-gather` substep via `job.step(name, "gather-must-gather")`, then
downloads via `step.get_artifact("must-gather.tar")`. Must-gather is not
downloaded by default; use `--auto-must-gather` during discovery or the
`must-gather` subcommand on an existing build directory.

### Pagination
`discovery.get_next_page_url()` extracts the "Older Runs" link. The buildId query parameter
references the oldest build on the current page.

### Adding new subcommands
1. Add a new subparser in `cli.parse_args()`
2. Implement `cmd_newmode(args, output_dir)` in `cli.py`
3. Set `set_defaults(func=cmd_newmode)`

### Incremental Downloads
The `prow.import_from_spyglass()` function is idempotent: if both `job.json` and `steps.json`
exist in the build directory, it returns a `Job` immediately without fetching.
Individual artifact downloads are also idempotent:
- `step.get_log()`: Skipped if `build-log.txt` already exists on disk
- `step.get_artifact(path)`: Skipped if the artifact file already exists on disk
- `must-gather/`: Skipped if directory exists (both --auto-must-gather and must-gather command)
- `hypershift-dumps/`: Skipped if directory exists (both --auto-hypershift and hypershift-dump command)

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
Each entry has `success: bool`. Multi-stage tests also have
`substeps: { inner_step_name: { success: bool }, ... }`.
Use `job.step(name)` or `job.step(name, inner_name)` to get `Step` objects.
Use `job.failed_steps()` to get all failed inner steps as `Step` objects.

## Error Handling

| Scenario | Action |
|----------|--------|
| Network error | Retry 3x with backoff, then fail |
| allBuilds not in HTML | Exit with error (page structure changed) |
| junit_operator.xml 404 | JobImportError (no inner steps available) |
| junit_operator.xml unparseable | JobImportError |
| build-log.txt 404 | Info log (expected), continue |
| must-gather 404 | Info log (expected), continue |
| Extraction fails | Warn, keep tar for inspection |
| Fewer builds than requested | Warn with counts, continue |
| Invalid URL in urls mode | Error log, skip that URL |
| Auth required, no gssapi | Error: install with `uv sync --extra kerberos` |
| Auth required, no Kerberos ticket | Error: run `kinit` and retry |
| Auth redirect to untrusted domain | Error: use `--trusted-redirect-domain` |
| Auth redirect loop | Error with URL (keyed on method+url+cookies) |
| Cached cookie expired | Clear cache for that domain, re-authenticate |

## Testing
```bash
# Verify help text
uv run dredge --help

# Test urls mode
uv run dredge urls -d ./artifacts \
    "https://prow.ci.openshift.org/view/gs/origin-ci-test/logs/periodic-ci-openshift-release-master-ci-4.22-e2e-azure-ovn-upgrade/2016123606924267520"

# Test history mode - failures only
uv run dredge history -d ./artifacts \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    2 --failure

# With all auto artifact downloads
uv run dredge history -d ./artifacts --auto \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    5

# Standalone must-gather on existing build directory
uv run dredge must-gather ./artifacts/<build_id>

# Standalone hypershift-dump on existing build directory
uv run dredge hypershift-dump ./artifacts/<build_id>

# Authenticated Prow deck (requires kinit + gssapi)
uv run dredge urls -d ./artifacts \
    "https://qe-private-deck-ci.apps.ci.l2s4.p1.openshiftapps.com/view/gs/qe-private-deck/..."
```

## Prow Job Artifact Structure References

The artifacts this tool downloads are produced by `ci-operator` in [openshift/ci-tools](https://github.com/openshift/ci-tools). Key source files:

- **`junit_operator.xml` generation**: `cmd/ci-operator/main.go` — `writeJUnit(suites, "operator")` saves ci-operator's own execution report. Each multi-stage test step becomes a `<testcase>` named `"Run multi-stage test {test} - {test}-{step} container test"`. Failure output is the step's error string, which includes the kubelet's truncated container termination message (not the full log).

- **Step execution and error construction**: `pkg/steps/run.go` — `Run()` executes the step graph as a DAG, creating one junit test case per step from `err.Error()`. `pkg/steps/multi_stage/run.go` — `runPod()` constructs the error for failed multi-stage steps, wrapping the result from `pkg/util/pods.go` `WaitForPodCompletion()` → `processPodEvent()`.

- **Step graph JSON**: `pkg/api/graph.go` — `CIOperatorStepDetails` / `CIOperatorStepDetailInfo` define the schema for `ci-operator-step-graph.json`. Includes `name`, `failed`, `started_at`, `finished_at`, `duration`, `dependencies`. Note: `substeps` field exists in the struct but is not populated for multi-stage tests in practice.

- **Artifact directory layout**: Multi-stage test artifacts land under `artifacts/{test_name}/{inner_step}/`. The inner step directory name is the step name with the test name prefix stripped (e.g. step `e2e-aws-ovn-openshift-e2e-test` → directory `openshift-e2e-test`). Each step directory contains `build-log.txt` (complete container log) and an `artifacts/` subdirectory with step-produced files (junit XML, etc.).

- **Per-container sub-tests**: `pkg/steps/artifacts.go` — `TestCaseNotifier.SubTests()` generates per-container junit entries from the `ci-operator.openshift.io/container-sub-tests` pod annotation. Failure output is `state.Terminated.Message`, subject to kubelet's 4KB termination message limit.

## Dependencies
- Python 3.10+
- `requests` library (managed via pyproject.toml / uv)
- `gssapi` (optional, for Kerberos auth): `uv sync --extra kerberos`

### Dev Dependencies
Installed via `uv sync --dev`:
- `pytest` + `responses` — testing
- `ruff` — linting and formatting
- `mypy` + `types-requests` — type checking
