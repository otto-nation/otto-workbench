---
name: pr-rebase
description: "AI-assisted rebase onto the branch's base with conflict resolution and force push. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against its base, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording (use task commit:reword instead)."
source: otto-workbench/ai/skills/pr-rebase/SKILL.md
invocation: "/pr-rebase [branch] [--no-fix] [--no-push] [--force] [--onto|--base <ref>]"
trigger: "Use when user asks to rebase a branch, resolve rebase conflicts, update a branch against its base, or fix merge conflicts during rebase."
skip: "Do not use for simple git pull --rebase with no conflicts. Do not use for commit rewording (use task commit:reword instead)."
output_schema:
  tool: pr-rebase
---

# PR Rebase

Rebases a feature branch onto its base. The `pr rebase` script handles
everything: fetch, rebase, AI-assisted conflict resolution (via `claude -p`),
and force-push.

The base is resolved per run, most authoritative source first: `--onto` when
given, then the branch's PR base branch as GitHub reports it, then the repo's
default branch. A stacked or release-branch PR is replayed onto its own base,
and a repo whose trunk is not `main` onto its own trunk.

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
- `--force` (optional): Rebase a branch the preflight refuses — one whose work
  already landed on its base, one sharing no history with it, or one conflicting
  past the file budget. Only pass it when the user has seen the exit-4 refusal
  and asked for the rebase anyway — never add it speculatively.
- `--onto <ref>` (also spelled `--base`, optional): Rebase onto this ref
  verbatim, overriding the PR base and the default branch. Only pass it when
  the user names a base; the resolved default is right otherwise.

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

**Exit 4 — the rebase was refused, nothing was rebased or pushed.** Parse the JSON:

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

`signal` names the evidence, and `status` groups the signals by what they found:

| `signal` | `status` | What it found |
|---|---|---|
| `pr_merged` | `already_landed` | GitHub reports the PR merged |
| `empty_diff` | `already_landed` | The branch has commits but no diff against its base — what a squash merge leaves behind |
| `commits_upstream` | `already_landed` | Every commit already has an equivalent upstream by patch id |
| `no_merge_base` | `unrelated_history` | The branch and its base share no commit at all |
| `conflicts_over_budget` | `conflicts_over_budget` | The rebase conflicted across more files than automatic resolution should attempt; it was aborted |

`commits_ahead` is a count on the two git signals and `null` on `pr_merged`: the
tracker is asked before the branch is checked out, so there is no honest count
to report there. `pr_number` is set on `pr_merged` only.

Report `detail` and stop. What to suggest depends on `status`:

- `already_landed` — rebasing would replay landed work and force-push a branch
  the merge deleted. Suggest deleting the worktree and branch instead.
- `unrelated_history` — the branch descends from a different root, usually one
  left by a re-initialised repo. Rebasing would replay its entire history onto a
  base it has nothing in common with. Suggest cherry-picking the wanted commits
  onto a fresh branch instead.
- `conflicts_over_budget` — a branch conflicting this widely has usually had its
  work land in another shape. The rebase was already aborted, so the worktree is
  clean; suggest checking whether the work is still wanted before forcing it.

If the user confirms they want the rebase anyway, re-run with the flag in
`override`:

```bash
pr rebase --fix --force --branch <branch>
```

`--force` waives every one of these checks, not just the one that fired. A
resumed rebase (one already paused in the worktree) waives the conflict budget
on its own: the conflicts are already there to resolve, and refusing would
strand the worktree mid-rebase.

**Exit 1 — error.** Report the error from stderr. Two of its shapes leave the
rebase done and only the push outstanding — both end at step 3, never at a
`git push`:

- **The pre-push hook refused the push.** The JSON reads `"status": "completed"`
  with `"force_pushed": false`, and the hook output above it names the check that
  failed. Diagnose that check, fix it, commit the fix, then finish at step 3.
  Re-running with `--fix` only repeats the AI recovery that already failed here.
- **`Recovery left uncommitted changes — not pushing`.** A push-recovery step left
  edits outside any commit, so the branch was deliberately left unpushed
  (`force_pushed` is `false`). Pre-push hooks validate the worktree rather than the
  commits, so pushing there would green-light a HEAD no hook saw. Report the listed
  paths, let the user commit or discard them, then finish at step 3.

### 3. Finish the push

A run that ends with the rebase complete and `force_pushed` false is finished
through the owner, once whatever blocked the push is committed:

```bash
pr rebase --branch <branch>
```

This is the same command step 1 calls `--no-fix` mode, and it is not report-only
here: the flag governs conflict resolution, not the push. A replay with nothing
to resolve force-pushes either way, and there is nothing left to resolve once the
rebase has completed. So the run re-fetches, replays nothing unless the base
moved again, and pushes — which puts the pre-push hooks on the HEAD that is
actually going out. This is how the skill ends. Never run `git push --force-with-lease` yourself,
and never hand it to the user as the remaining step: `pr rebase` prints a
`Resume: git -C '<worktree>' push --force-with-lease` line when a push is refused,
and that hint is for a human at a terminal, not an instruction to you.

The one exception is `--no-push`, where the user asked for the push to be withheld
— report the printed command as the script gave it, and come back here only if
they then ask for the branch to be pushed.

---

## Constraints

- Always call `pr rebase` (the dispatcher, two words), never `pr-rebase`
  (the backing script) — the dispatcher handles context resolution and routing
- Never run raw `git push --force-with-lease`, and never end a run by handing that
  command to the user — `pr rebase` force-pushes by default, and step 3 finishes any
  run that ended unpushed. `--no-push` is the only run that ends on the printed
  command, because that is what the user asked for
- A fresh rebase auto-stashes the worktree, untracked files included, and restores
  it afterwards; the pre-push hooks then validate the branch alone. A resumed
  rebase cannot stash (the index is mid-rebase), so uncommitted work is still
  present while its hooks run
