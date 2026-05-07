---
name: prow-artifacts
description: Download Prow CI job artifacts for analysis. Use when user asks to examine failing CI jobs, provides Prow URLs, mentions a PR's test failures, or asks about prow/CI logs.
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
- **`steps.json`** — hierarchical step structure with success/failure status

**Always read `steps.json` first.** It shows every step that ran, whether it passed, and for multi-phase tests, the substeps nested under a parent step. Example structure:

```json
{
  "src": { "success": true },
  "e2e-aws-capi-techpreview": {
    "success": false,
    "substeps": {
      "test": { "success": false },
      "gather-must-gather": { "success": true }
    }
  }
}
```

### Step directories

Each downloaded step has a directory at `.dredge/<build_id>/<parent_step>/<step_name>/` containing a `build-log.txt` with the direct output of that step.

- **Test steps** (e.g. `test`): the log usually contains everything you need — test output, failure messages, stack traces.
- **Artifact-gathering steps** (e.g. `gather-must-gather`, `gather-extra`): the log is usually not interesting. The value is in the artifacts, located under `artifacts/` within the step directory.

### Downloading must-gather separately

If must-gather was not downloaded during import, fetch it explicitly:

```bash
dredge -d "$(pwd)/.dredge" fetch-must-gather <build_id>
```

## Known substeps

| Substep | Type | Description | Guide |
|---|---|---|---|
| `gather-must-gather` | Artifact collection | OpenShift cluster diagnostic snapshot: node state, resource dumps, etcd health, operator logs, networking diagnostics | [gather-must-gather](references/gather-must-gather.md) |
