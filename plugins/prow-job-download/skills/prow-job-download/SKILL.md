---
name: prow-job-download
description: Download Prow CI job logs using the dredge tool. Use when user asks to examine failing CI jobs, provides Prow URLs, mentions a PR's test failures, or asks about prow/CI logs. Handles PR jobs, specific Spyglass URLs, and job history pages.
---

# Prow Job Download

Download Prow CI job logs with `dredge` and make them available for analysis.

## Quick start

1. Identify the mode from user input
2. Create a working directory under `.dredge/` in CWD
3. Run `dredge` to download logs
4. Report what was downloaded

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

## On-demand artifact download

During analysis, if must-gather or hypershift logs are needed, fetch them from an already-downloaded build:

```bash
# Must-gather (auto-detects the step)
dredge must-gather <workdir>/<build_id>

# Hypershift hosted cluster dumps (auto-detects the step)
dredge hypershift-dump <workdir>/<build_id>
```

These commands use `build_info.json` metadata already present in the build directory.

## Checklist

- [ ] Identified correct mode from user input
- [ ] Created `.dredge/<name>/` directory
- [ ] Ran `dredge` with correct subcommand and flags
- [ ] Read `build_info.json` from each build directory
- [ ] Reported build IDs, failed steps, and artifact paths to calling agent
