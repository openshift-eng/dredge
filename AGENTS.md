# dredge — Prow Job Downloader

## Purpose
Downloads artifacts from Prow CI jobs for analysis.

## Project Layout

```
pyproject.toml              - Project metadata and dependencies (uv/hatch)
src/dredge/
  __init__.py
  __main__.py               - python -m dredge entry point
  cli.py                    - CLI argument parsing, command handlers, main()
  http.py                   - HTTP primitives (fetch, download, directory listing, auth-aware session)
  auth.py                   - OAuth proxy detection, auth chain follower, Kerberos, cookie cache
  prow.py                   - Prow URL handling, build discovery, pagination
  artifacts.py              - Artifact discovery, download, extraction, build processing
  github.py                 - GitHub API integration (PR job fetching)
```

## Architecture

### Data Flow
1. Fetch job history page HTML from Prow (history mode) or parse Spyglass URLs directly (urls mode)
2. Extract `var allBuilds = [...]` JSON via regex (history mode only)
3. Filter builds by result (optional: --failure, --success, or both)
4. For each build:
   - Convert SpyglassLink to GCS path (strip "/view/gs/" prefix)
   - **Download junit_operator.xml if not already present**
   - **Fetch and save ci-operator-step-graph.json if not already present**
   - **Build step hierarchy from junit + step graph; write build_info.json (with steps)**
   - **Download build-log.txt and junit XML for failed steps (from hierarchy)**
   - **Discover and download must-gather using step hierarchy (deterministic path)**
   - **Discover and download hypershift dumps using step hierarchy**
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

### Subcommands

**history** - Download from job history page:
```bash
# Download most recent N jobs (any result)
uv run dredge history <url> <count>

# Download only failed jobs
uv run dredge history <url> <count> --failure

# Download only successful jobs
uv run dredge history <url> <count> --success

# Download jobs matching either result (excludes PENDING, ABORTED, etc.)
uv run dredge history <url> <count> --failure --success
```

**urls** - Download specific builds by Spyglass URL:
```bash
uv run dredge urls <url> [<url> ...]
```

**pr** - Download failed prow jobs from a GitHub PR:
```bash
uv run dredge pr <github_pr_url>
```

### Options
- `-o, --output-dir`: Output directory (default: current directory)
- `--trusted-redirect-domain`: Additional trusted domain for auth redirects (may be repeated; prefix with `.` for suffix match)

## Common Modifications

### Adding new artifact types
1. Add download logic in `artifacts.process_build()`
2. Handle 404 gracefully (some builds may not have the artifact)

### Changing must-gather discovery
The `artifacts.discover_must_gather()` function uses the step hierarchy (built
from junit + step graph) to find `gather-must-gather` inner steps, then returns
a deterministic GCS path without HTTP directory listing.

### Pagination
`prow.get_next_page_url()` extracts the "Older Runs" link. The buildId query parameter
references the oldest build on the current page.

### Adding new subcommands
1. Add a new subparser in `cli.parse_args()`
2. Implement `cmd_newmode(args, output_dir)` in `cli.py`
3. Set `set_defaults(func=cmd_newmode)`

### Incremental Downloads
The tool skips downloading individual artifacts that already exist:
- `junit_operator.xml`: Skipped if file exists
- `ci-operator-step-graph.json`: Skipped if file exists (parsed from cache)
- `build-logs/`: Skipped if directory exists
- `must-gather/`: Skipped if directory exists
- `hypershift-dumps/`: Skipped if directory exists

The `build_info.json` metadata file is always updated (including the `steps`
hierarchy). To force re-download of an artifact, delete that specific file
or directory.

### Build Metadata
Each build directory contains `build_info.json` with:
- `build_id`: The Prow build ID
- `execution_date`: When the build ran (ISO 8601)
- `prow_job_link`: Link to the Prow job page (Spyglass view)
- `pr_link`: GitHub PR URL (if PR job)
- `commit_link`: GitHub commit URL being tested
- `steps`: Hierarchical step structure keyed by test name, each with:
  - `failed`, `started_at`, `finished_at`, `duration_seconds`, `dependencies`
  - `inner_steps`: dict of inner step names to `{failed: bool}`

## Error Handling

| Scenario | Action |
|----------|--------|
| Network error | Retry 3x with backoff, then fail |
| allBuilds not in HTML | Exit with error (page structure changed) |
| junit_operator.xml 404 | Warn, continue |
| junit_operator.xml unparseable | Warn, skip failed step artifact download |
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
uv run dredge urls \
    "https://prow.ci.openshift.org/view/gs/origin-ci-test/logs/periodic-ci-openshift-release-master-ci-4.22-e2e-azure-ovn-upgrade/2016123606924267520"

# Test history mode - failures only
uv run dredge history \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    2 --failure

# With custom output directory
uv run dredge -o ./artifacts history \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    5

# Authenticated Prow deck (requires kinit + gssapi)
uv run dredge urls \
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
