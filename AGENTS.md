# Prow Job Downloader

## Purpose
Downloads artifacts from Prow CI jobs for analysis.

## Architecture

### Data Flow
1. Fetch job history page HTML from prow.ci.openshift.org (history mode) or parse Spyglass URLs directly (urls mode)
2. Extract `var allBuilds = [...]` JSON via regex (history mode only)
3. Filter builds by result (optional: --failure, --success, or both)
4. For each build:
   - Convert SpyglassLink to GCS path (strip "/view/gs/" prefix)
   - **Download junit_operator.xml if not already present**
   - List artifacts directory to discover must-gather location
   - **Download and extract must-gather if not already present**
   - **Always write/update build_info.json with PR link, commit link, execution date**
5. Follow "Older Runs" pagination link if more builds needed (history mode)

### Key URL Transformations
- SpyglassLink: `/view/gs/BUCKET/PATH` -> GCS path: `BUCKET/PATH`
- Direct download: `https://storage.googleapis.com/{gcs_path}/...`
- Directory listing: `https://gcsweb-ci.apps.ci.l2s4.p1.openshiftapps.com/gcs/{gcs_path}/...`

### External Dependencies
- Prow job history page structure (var allBuilds JSON)
- GCS bucket public access via storage.googleapis.com
- gcsweb directory listing HTML format

## CLI Usage

### Subcommands

**history** - Download from job history page:
```bash
# Download most recent N jobs (any result)
python download_prow_job.py history <url> <count>

# Download only failed jobs
python download_prow_job.py history <url> <count> --failure

# Download only successful jobs
python download_prow_job.py history <url> <count> --success

# Download jobs matching either result (excludes PENDING, ABORTED, etc.)
python download_prow_job.py history <url> <count> --failure --success
```

**urls** - Download specific builds by Spyglass URL:
```bash
python download_prow_job.py urls <url> [<url> ...]
```

### Options
- `-o, --output-dir`: Output directory (default: current directory)

## Common Modifications

### Adding new artifact types
1. Add download logic in `process_build()`
2. Handle 404 gracefully (some builds may not have the artifact)

Example:
```python
# In process_build(), after junit download:
build_log_url = f"https://storage.googleapis.com/{gcs_path}/build-log.txt"
build_log_dest = build_dir / "build-log.txt"
download_artifact(build_log_url, build_log_dest)
```

### Changing must-gather discovery
The `discover_must_gather()` function parses gcsweb HTML to find subdirectories,
then checks for `{subdir}/gather-must-gather/artifacts/must-gather.tar`.

To search for a different pattern:
```python
# Modify the path pattern in discover_must_gather()
must_gather_path = f"{subdir_name}/your-step-name/artifacts/your-file.tar"
```

### Pagination
`get_next_page_url()` extracts the "Older Runs" link. The buildId query parameter
references the oldest build on the current page.

### Adding new subcommands
1. Add a new subparser in `parse_args()`
2. Implement `cmd_newmode(args, output_dir)`
3. Set `set_defaults(func=cmd_newmode)`

No changes needed to main() or existing commands.

### Incremental Downloads
The script skips downloading individual artifacts that already exist:
- `junit_operator.xml`: Skipped if file exists
- `must-gather/`: Skipped if directory exists

The `build_info.json` metadata file is always updated. To force re-download
of an artifact, delete that specific file or directory.

### Build Metadata
Each build directory contains `build_info.json` with:
- `build_id`: The Prow build ID
- `execution_date`: When the build ran (ISO 8601)
- `prow_job_link`: Link to the Prow job page (Spyglass view)
- `pr_link`: GitHub PR URL (if PR job)
- `commit_link`: GitHub commit URL being tested

## File Structure

```
download_prow_job.py
├── parse_args()           - CLI arguments with subparsers (history, urls)
├── setup_logging()        - Timestamped logging
├── fetch_page()           - HTTP GET with retries
├── extract_builds()       - Regex extract `var allBuilds = [...]` JSON
├── filter_builds()        - Filter by Result (FAILURE, SUCCESS, or both)
├── get_next_page_url()    - Find "Older Runs" link
├── spyglass_to_gcs_path() - Convert to GCS path
├── parse_spyglass_url()   - Extract build ID and path from Spyglass URL
├── download_artifact()    - Stream download, return False on 404
├── discover_must_gather() - List artifacts dir, find must-gather path
├── extract_tgz()          - Extract .tar as gzip
├── write_build_metadata() - Write build_info.json with PR/commit links
├── process_build()        - Download artifacts for one build
├── collect_builds()       - Paginate to collect N builds with filtering
├── cmd_history()          - Handler for 'history' subcommand
├── cmd_urls()             - Handler for 'urls' subcommand
└── main()                 - Parse args, dispatch to subcommand handler
```

## Error Handling

| Scenario | Action |
|----------|--------|
| Network error | Retry 3x with backoff, then fail |
| allBuilds not in HTML | Exit with error (page structure changed) |
| junit_operator.xml 404 | Warn, continue |
| must-gather 404 | Info log (expected), continue |
| Extraction fails | Warn, keep tar for inspection |
| Fewer builds than requested | Warn with counts, continue |
| Invalid URL in urls mode | Error log, skip that URL |

## Testing
Run with a small count (2-3) against a known job history URL.
Verify directory creation, artifact downloads, and must-gather extraction.

```bash
# Test history mode - all jobs (default behavior)
python download_prow_job.py history \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    2

# Test history mode - failures only
python download_prow_job.py history \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    2 --failure

# Test history mode - successes only
python download_prow_job.py history \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    2 --success

# Test urls mode
python download_prow_job.py urls \
    "https://prow.ci.openshift.org/view/gs/origin-ci-test/logs/periodic-ci-openshift-release-master-ci-4.22-e2e-azure-ovn-upgrade/2016123606924267520"

# With custom output directory
python download_prow_job.py -o ./artifacts history \
    "https://prow.ci.openshift.org/job-history/gs/test-platform-results/logs/JOB_NAME" \
    5

# Verify help text
python download_prow_job.py --help
python download_prow_job.py history --help
python download_prow_job.py urls --help
```

## Dependencies
- Python 3.7+
- `requests` library (`pip install requests`)
