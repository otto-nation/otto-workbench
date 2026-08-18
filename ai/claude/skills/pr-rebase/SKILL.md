---
name: pr-rebase
description: "AI-assisted rebase onto origin/main with conflict resolution and force push. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording (use task commit:reword instead)."
source: otto-workbench/ai/claude/skills/pr-rebase/SKILL.md
invocation: "/pr-rebase [branch] [--no-fix] [--no-push] [--force]"
trigger: "Use when user asks to rebase a branch, resolve rebase conflicts, update a branch against main, or fix merge conflicts during rebase."
skip: "Do not use for simple git pull --rebase with no conflicts. Do not use for commit rewording (use task commit:reword instead)."
output_schema:
  tool: pr-rebase
---

# PR Rebase

Rebases a feature branch onto origin/main. The `pr rebase` script handles
everything: fetch, rebase, AI-assisted conflict resolution (via `claude -p`),
and force-push.

Run with `/pr-rebase` or `/pr-rebase <branch>`.

---

## Arguments

- `branch` (optional): Target branch to rebase. Passed as `--branch` to
  `pr rebase`, which resolves the worktree automatically. When omitted, the
  current branch is used.
- `--no-fix` (optional): Report conflicts without resolving them. By default,
  conflicts are resolved with AI and force-pushed automatically.
- `--no-push` (optional): Do everything except push. Composes with `--no-fix`
  independently — under the default auto-fix mode the AI still resolves conflicts,
  and the force-push command is printed for the user to run.
- `--force` (optional): Rebase a branch whose work already landed on
  `origin/main`. Only pass it when the user has seen the exit-4 refusal and
  asked for the rebase anyway — never add it speculatively.

---

## Steps

### 1. Run pr rebase

- **Default mode** (auto-fix):

```bash
pr rebase --fix --branch <branch>
```

- **`--no-fix` mode** (report only):

```bash
pr rebase --branch <branch>
```

`--no-push` composes with either: the rebase runs (and the AI still resolves
conflicts under `--fix`), but nothing reaches the remote — the force-push command
is printed for the user to run instead.

When no branch argument is provided, omit `--branch` (uses CWD's branch).

JSON output is on stdout; status messages are on stderr.

### 2. Handle the result

**Exit 0 — success.** Parse the JSON output:

```json
{
  "status": "clean",
  "commits_replayed": 22,
  "conflicts_resolved": 2,
  "files_resolved": ["orc-lending/go.mod", "orc-lending/go.sum"],
  "files_stale": ["orc-lending/go.sum"],
  "force_pushed": true
}
```

`commits_replayed` counts only commits replayed from the branch — commits the
push recovery adds (regeneration, check fixes) are excluded. `conflicts_resolved`
counts conflicted-file resolutions, matching `files_resolved`.

Report commits replayed and any conflicts resolved. When `files_stale` is
non-empty, those files were staged from the incoming side but their
regeneration command failed — say so and tell the user to regenerate them
manually. Done.

**Exit 3 — conflicts detected (`--no-fix` mode only).** Parse the JSON:

```json
{
  "status": "conflicts",
  "files": ["src/auth.py", "tests/test_auth.py"],
  "rebase_head": "abc1234",
  "rebase_head_subject": "fix: auth token refresh",
  "remaining_commits": 3
}
```

Report what was found. Ask the user if they want AI resolution. If yes:

```bash
pr rebase --fix --branch <branch>
```

This resumes the in-progress rebase with AI conflict resolution and force-pushes.

**Exit 4 — branch already landed, nothing was rebased or pushed.** Parse the JSON:

```json
{
  "branch": "isaac/626/unified_workbench_config",
  "signal": "pr_merged",
  "detail": "PR #726 is merged (https://github.com/owner/repo/pull/726)",
  "commits_ahead": null,
  "pr_number": 726,
  "status": "already_landed",
  "override": "--force"
}
```

`signal` names the evidence: `pr_merged` (GitHub reports the PR merged),
`empty_diff` (the branch has commits but no diff against `origin/main` — what a
squash merge leaves behind), or `commits_upstream` (every commit already has an
equivalent upstream by patch id).

`commits_ahead` is a count on the two git signals and `null` on `pr_merged`: the
tracker is asked before the branch is checked out, so there is no honest count
to report there. `pr_number` is set on `pr_merged` only.

Report `detail` and stop. Rebasing here would replay landed work and force-push
a branch the merge deleted. Suggest deleting the worktree and branch instead. If
the user confirms the branch was deliberately reopened for follow-up work, re-run
with the flag in `override`:

```bash
pr rebase --fix --force --branch <branch>
```

**Exit 1 — error.** Report the error from stderr.

When the failure is `Recovery left uncommitted changes — not pushing`, the rebase
itself succeeded but a push-recovery step left edits outside any commit, so the
branch was deliberately left unpushed (`force_pushed` is `false`). Pre-push hooks
validate the worktree rather than the commits, so pushing there would green-light
a HEAD no hook saw. Report the listed paths and let the user commit or discard
them before re-running.

---

## Constraints

- Always call `pr rebase` (the dispatcher, two words), never `pr-rebase`
  (the backing script) — the dispatcher handles context resolution and routing
- Never run raw `git push --force-with-lease` — `pr rebase` force-pushes by default,
  and with `--no-push` it prints the command for the user rather than issuing it
- A fresh rebase auto-stashes the worktree, untracked files included, and restores
  it afterwards; the pre-push hooks then validate the branch alone. A resumed
  rebase cannot stash (the index is mid-rebase), so uncommitted work is still
  present while its hooks run
