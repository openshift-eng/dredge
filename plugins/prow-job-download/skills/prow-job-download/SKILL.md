---
name: prow-job-download
description: Download Prow artifacts for summarisation or deep analysis. Use when user asks to examine or summarise failing CI jobs, provides Prow URLs, mentions a PR's test failures, or asks about prow/CI logs. Handles PR jobs, specific Spyglass URLs, and job history pages.
---

# Prow Job Download

Download Prow CI job logs with `dredge` and make them available for analysis.

## Quick start

1. Identify the mode from user input
2. Create a working directory under `.dredge/` in CWD
3. Run `dredge` to download logs
4. Report what was downloaded

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

Then proceed with the resolved URL using the appropriate mode below.

## Determine mode

| User input | Mode | Command |
|---|---|---|
| GitHub PR URL (`github.com/org/repo/pull/N`) | pr | `dredge pr` |
| Prow Spyglass URL(s) containing `/view/gs/` | urls | `dredge urls` |
| Prow job-history URL containing `/job-history/` | history | `dredge history` |

## Working directory

Create `.dredge/<name>/` under CWD. Use absolute paths when passing to `-d`. Names are deterministic so repeated requests reuse the same directory (dredge skips already-downloaded artifacts).

- **pr**: `.dredge/pr-<org>-<repo>-<number>/`
- **urls**: `.dredge/urls-<job-name-fragment>-<short-build-id>/`
- **history**: `.dredge/history-<job-name-fragment>-<count>/`

## Download commands

```bash
# PR — downloads all failed jobs for the PR
dredge pr -d <workdir> <github_pr_url>

# Specific Spyglass URLs
dredge urls -d <workdir> <url> [<url> ...]

# Job history — most recent N failures
dredge history -d <workdir> <job_history_url> <count> --failure
```

## After download

Each build directory contains `build_info.json` with step hierarchy and failure status. Read this to understand what failed.

- **Failed step logs**: `<workdir>/<build_id>/build-logs/<step-name>/build-log.txt`
- **Step junit**: `<workdir>/<build_id>/build-logs/<step-name>/junit/`

List the build directories and summarize available artifacts for the calling agent.

## Cluster logs (must-gather / hypershift-dump)

When the user asks about cluster state, node conditions, operator health, pod status, events, resource errors, etcd, networking, storage, or any question that requires detail beyond what build-logs and junit provide, **you must download cluster logs before answering**. Do not attempt to answer cluster-level questions from build-log output alone.

Pick the right command based on the job type:

| Signal | Command |
|---|---|
| Standard OCP/OpenShift job (non-HyperShift) | `dredge must-gather <workdir>/<build_id>` |
| HyperShift / hosted-cluster job | `dredge hypershift-dump <workdir>/<build_id>` |
| Unsure | Run both; only the applicable one will produce output |

```bash
# Must-gather (auto-detects the step)
dredge must-gather <workdir>/<build_id>

# Hypershift hosted cluster dumps (auto-detects the step)
dredge hypershift-dump <workdir>/<build_id>
```

These commands use `build_info.json` metadata already present in the build directory. They download additional artifacts into the build directory and may take a moment to complete.

## Checklist

- [ ] Identified correct mode from user input
- [ ] Created `.dredge/<name>/` directory
- [ ] Ran `dredge` with correct subcommand and flags
- [ ] Read `build_info.json` from each build directory
- [ ] Reported build IDs, failed steps, and artifact paths to calling agent
