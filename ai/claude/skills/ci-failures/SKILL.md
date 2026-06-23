---
name: ci-failures
description: "Diagnose and fix GitHub Actions CI failures with run-aware progression tracking: fetch, classify, diagnose, fix, push, and monitor across workflow runs. TRIGGER when: user asks about CI failures, broken builds, failing checks, or wants to fix CI on their PR branch; CI checks fail after a push; user asks why CI is red. SKIP: reviewing code (use code-review or pr review instead); addressing PR review comments (use pr-comments instead)."
source: otto-workbench/ai/claude/skills/ci-failures/SKILL.md
invocation: "/ci-failures [<pr_number_or_run_id_or_branch>]"
trigger: "Use when user asks about CI failures, broken builds, failing checks, or wants to fix CI on their PR branch; CI checks fail after a push; user asks why CI is red."
skip: "Do not use for code review (use code-review or pr review instead); do not use for addressing PR review comments (use pr-comments instead)."
---

# CI Failures

Diagnoses and fixes GitHub Actions failures on PR branches with full lifecycle
tracking. Fetches run data, classifies failures by kind, diagnoses root causes,
applies fixes, pushes, and tracks progression across workflow runs.

Run with `/ci-failures`, `/ci-failures <pr_number>`, `/ci-failures <run_id>`,
or `/ci-failures <branch_name>`.

---

## Arguments

- `pr_number_or_run_id_or_branch` (optional): PR number, GitHub Actions run ID, or
  branch name to diagnose. Defaults to auto-detection from the current branch.
  - Numeric values are treated as PR numbers (small) or run IDs (large)
  - Values containing `/` are treated as branch names

---

## Steps

### 1. Resolve the argument

If a skill argument was provided that looks like a branch name (contains `/` or
is not purely numeric), resolve it first:

```bash
resolve-branch "<argument>"
```

This tries exact match, worktree directory match, separator normalization
(`-` → `/`), and fuzzy search — in that order.
- **Success (exit 0)**: use the stdout output as the resolved branch name
- **Multiple matches (exit 1)**: candidates are listed on stderr — show them
  and ask the user to pick
- **No matches (exit 1)**: error and stop

Purely numeric arguments skip resolution — they are PR numbers or run IDs.

### 2. Fetch status and display dashboard

Run the CI check script to fetch the latest run, classify failures, and display status.

Route the resolved argument to the correct `ci-check` flag:

- **Branch name**: `ci-check --branch <resolved_name> 2>&1`
- **PR number** (small integer): `ci-check --pr <number> 2>&1`
- **Run ID** (large integer): `ci-check --run <run_id> 2>&1`
- **No argument**: `ci-check 2>&1`

The script auto-detects the repo, branch, and worktree root from CWD. Only pass `--repo-dir <path>` when CWD is outside the target worktree (e.g., in a bare repo).

The script outputs:
- **stderr:** Human-readable dashboard (run number, commit, failure counts by kind, progression)
- **stdout:** Structured JSON report with all failures, classifications, and progression markers

**Invocation rules:** Run the command as a single simple statement. Do not use command substitution `$(...)` or pipes to resolve arguments — the scripts handle all resolution internally. Capture both stderr and stdout together with `2>&1`. The dashboard text appears first, followed by the JSON report starting with `{` on its own line — parse from there.

If all checks pass (conclusion is "success" and no failures), report that CI is green and stop.

### 3. Classify and group failures

From the JSON report, present failures grouped by kind in priority order:

1. **Regressed** — failures that were fixed but came back (urgent)
2. **Persisting** — failures from prior run that survived a fix attempt
3. **New** — first-time failures

Present the classification table for user confirmation:

```
## CI Failures — Run #7 (abc1234)

| # | Kind | Job | File | Line | Progression | Summary |
|---|------|-----|------|------|-------------|---------|
| 1 | lint | shellcheck | bin/foo.sh | 42 | new | SC2086: Double quote |
| 2 | test | pytest | tests/auth.py | 18 | persisting | AssertionError |
| 3 | infra | docker | — | — | new | connection refused |

Proceed with this classification? Override any kinds?
```

Allow the user to override classifications (e.g. "that test failure is flaky").

### 4. Diagnose

For each non-infra, non-flaky failure group:

- **lint:** The annotation usually contains the exact fix. Read the file to confirm context.
- **test:** Read both the test file and the code under test. Identify the mismatch — is the test wrong, or is the code wrong?
- **build:** Parse the log output. Look for missing dependencies, version mismatches, or config errors.
- **infra:** Flag to user with the raw error. Do not attempt a fix.
- **flaky:** Flag to user. Suggest re-running the workflow.

Present diagnosis per group. User confirms before fixing.

For persisting/regressed failures, include context from prior attempts:
> "Previously diagnosed as X, fix Y was applied at commit Z — still failing. The prior fix may have been incomplete or targeted the wrong root cause."

### 5. Apply fixes and push

For each confirmed diagnosis:
1. Edit the files to address the failure
2. After all edits, stage and commit:

```bash
git add <changed-files>
git commit -m "fix: address CI failures from run #<number>"
git push
```

Group all fixes into a single commit. The state file is updated automatically on the next `ci-check` invocation.

### 6. Monitor (on re-invocation)

When re-invoked after a push:
1. Fetch the new run triggered by the push
2. Compute progression against the prior run
3. Present delta:

```
## Progression — Run #8 vs Run #7

Resolved: 2 (shellcheck SC2086, pytest test_auth)
Persisting: 1 (pytest test_validate — was diagnosed as X, fix Y applied)
New: 1 (bats test_cleanup)
```

Re-enter Step 4 for persisting failures with context of what was already tried.

### 7. Report

Print:
- Number of fixes applied
- Commit SHA
- Number of failures resolved vs persisting
- Any infra/flaky failures that need manual action
- Link to the PR or run

---

## Failure Kinds

| Kind | Examples | Log depth | Fix strategy |
|------|----------|-----------|--------------|
| **lint** | ShellCheck, ESLint, yamllint | Annotations only | Direct code fix |
| **test** | pytest, bats, jest | Annotations; logs if missing file/line | Code or test fix |
| **build** | Docker, webpack, gradle | Full logs | Dependency/config fix |
| **infra** | Timeouts, rate limits, OOM | Full logs | Flag to user |
| **flaky** | Same test passed in prior run | Never | Flag, suggest re-run |

---

## Constraints

- NEVER apply fixes without user confirmation of diagnosis
- NEVER auto-fix infra or flaky failures
- Persisting failures on re-invocation include context of prior fix attempts
- Single commit per fix cycle
- State file lives at `<worktree>/ignore/ci-failures/state.json` — travels with the branch
- When overriding a classification, respect the user's judgment — if they say "flaky", treat it as flaky
