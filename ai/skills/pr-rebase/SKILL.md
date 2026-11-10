---
name: pr-rebase
description: "Rebase onto the branch's base with generated-only conflict rebuild or abort; --fix for AI resolution. TRIGGER when: user asks to rebase a branch, resolve rebase conflicts, update a branch against its base, or fix merge conflicts during rebase. SKIP: simple git pull --rebase with no conflicts; commit rewording."
source: otto-workbench/ai/skills/pr-rebase/SKILL.md
invocation: "/pr-rebase [branch] [--fix] [--no-push] [--stop-on-conflict] [--force] [--onto|--base <ref>] [--fork-point <ref>] [--no-verify]"
trigger: "Use when user asks to rebase a branch, resolve rebase conflicts, update a branch against its base, or fix merge conflicts during rebase."
skip: "Do not use for simple git pull --rebase with no conflicts. Do not use for commit rewording."
output_schema:
  tool: pr rebase
---

# PR Rebase

Rebases a feature branch onto its base. The `pr rebase` script handles
fetch, rebase, conflict handling, and force-push. Default mode rebuilds
declared generated files and lockfiles, then **aborts to a clean tree**
(exit 3) on any other conflict. Pass `--fix` for AI resolution. Pass
`--stop-on-conflict` to keep today's paused tree instead of aborting.
There is no `--no-fix` flag.

The base is resolved per run, most authoritative source first: `--onto` (or
`--base`) when given, then the branch's PR base branch as GitHub reports it,
then the branch this one is stacked on per local ancestry, then the repo's
default branch. A stacked or release-branch PR is replayed onto its own base, a
stack whose parent has no PR yet onto that parent, and a repo whose trunk is not
`main` onto its own trunk.

`--onto` is taken verbatim, so it may name any ref — `upstream/trunk`, a tag, a
SHA. The rungs below it yield a branch name that is resolved as
`origin/<name>`.

Run with `/pr-rebase` or `/pr-rebase <branch>`.

## Finish without asking

A request to rebase is a request for a rebased, pushed branch. Drive every
outcome below to that end yourself — escalate to `--fix`, resolve what the AI
could not, take a refusal's `remedy`, fix a failing pre-push check, and push
through step 3 — and report what you did once it is done. Do not stop to ask
whether to continue, whether to use AI, or whether to push.

Stop and hand back only where the next move is a decision the tooling exists to
leave to a person, and say exactly which one it is:

- an exit 4 that is not `partially_landed` — the branch already landed, shares
  no history with its base, or conflicts past the budget, so the only way on is
  `--force` or deleting the branch
- a resolution that would drop changes git merged cleanly and that you cannot
  rebuild hunk by hunk — `WORKBENCH_ALLOW_DROPPED_CHANGES=1` is the user's to set
- a pre-push check failure the branch caused and that you cannot fix
- anything the user asked to withhold: `--no-push`, `--stop-on-conflict`

---

## Arguments

- `branch` (optional): Target branch to rebase. Passed as `--branch` to
  `pr rebase`, which resolves the worktree automatically — and creates one with
  `wt switch` from any checkout when the branch has none, so there is no need
  to make it first. `--abort` and `--push-only` never create a worktree: they
  continue work that must already be in a checkout. When omitted, the current
  branch is used.
- `--fix` (optional): Resolve conflicts with AI and rebase (force-pushes
  unless `--no-push`). Pass it on the first run only when the user asked for AI
  resolution; otherwise run default mode first and escalate to it automatically
  on exit 3. Incompatible with
  `--stop-on-conflict`.
- `--stop-on-conflict` (optional): Pause on conflicts (stash held, tree
  dirty, exit 3) instead of aborting. Use when the user wants to inspect or
  finish the replay by hand. Default without `--fix` is abort, not pause.
- `--no-push` (optional): Do everything except push. Composes with `--fix`
  independently — under `--fix` the AI still resolves conflicts, and the
  force-push command is printed for the user to run.
- `--force` (optional): Rebase a branch the preflight refuses — one whose work
  already landed on its base, one sharing no history with it, or one conflicting
  past the file budget. Only pass it when the user has seen the exit-4 refusal
  and asked for the rebase anyway — never add it speculatively.
- `--onto <ref>` (also spelled `--base`, optional): Rebase onto this ref
  verbatim, overriding the PR base and the default branch. Only pass it when
  the user names a base; the resolved default is right otherwise.
- `--fork-point <ref>` (optional): Replay only the commits *after* `<ref>`,
  rather than the whole branch. This is git's `<upstream>` argument, which
  `--onto` alone cannot express — with one ref, git's `<newbase>` and
  `<upstream>` are the same and every commit since the merge base is replayed.
  Only pass it when a `partially_landed` refusal named the ref in its `remedy`
  field, or the user named one; it changes which commits end up on the branch.
  Not git's boolean `--fork-point`, and it is ignored on a resumed rebase.
- `--no-verify` (optional): Force-push without running the pre-push hook. The
  same rule as `pr create --draft --no-verify`: only for a hook failure already read
  and judged a flake or not the branch's doing, or when the user asks. It also
  skips the AI repair of pre-push failures, since no hook output exists to
  repair. Never add it to get past a failure nobody has read.

---

## Steps

### 0. Do not preflight

Go straight to step 1. `pr rebase` fetches, resolves its own base, detects an
in-progress rebase, and refuses the branches step 2's exit 4 describes — and it
records each of those decisions with its reason, so the answers arrive in the
trail rather than costing a turn each up front. Running `git fetch`,
`rev-list --count`, or `gh pr view` before it asks the script's own questions in
a worse form: the answer you get is from a second fetch, a moment earlier, with
no bearing on what the run then decides.

### 1. Run pr rebase

- **Default mode** (generated rebuild or abort; no AI):

  ```bash
  pr rebase --branch <branch>
  ```

  Generated-class files (`accept_theirs` with a declared `rebase.regenerate`,
  or lockfiles in `LOCKFILE_REGENERATORS`) rebuild and the rebase continues.
  The conventional `mise run generate` fallback does **not** qualify here — it
  stays `--fix`-only. Any other class, a missing declaration, a failed rebuild,
  or `files_stale` aborts (`tree: clean`, exit 3). Foreground is fine: there is
  no AI call.

- **`--fix` mode** (AI resolution). Escalate to this automatically when default
  mode exits 3; do not wait for the user to ask:

  ```bash
  pr rebase --fix --branch <branch>
  ```

  **Start this as a background job, not a foreground command** — the Bash tool's
  `run_in_background` under Claude Code, `job_start` under Pi. Use the harness's
  own job facility, never a detached `&` or `nohup`: a job it started messages you
  on completion, while a detached shell leaves you polling for an exit you are
  never told about. Give it 90 minutes.

  Ninety because that clears the longest run on record. Across 90 days of trails
  (`otto-log list --script pr-rebase --since 90d`), runs that hit at least one
  conflict took a median of under 4 minutes, but the p90 was 24 minutes and the
  slowest 74 — the distribution has a long tail, and the tail is what the bound
  has to cover. Per file, resolution is faster than it looks from the outside:
  10 seconds at the median, 83 at p90.

  So most runs finish well inside any harness's foreground timeout and the tail
  does not come close to it. Background it for the tail, not for the median. A
  timeout is not a clean retry: the kill does not abort the rebase, it leaves a
  partial one in the worktree, and the next run resumes it — announced on the
  console, but invisible in the JSON step 2 parses. What looks like "it timed
  out, run it again" is a mid-flight handoff to a second run that inherits an
  unfinished rebase.

  Backgrounding is safe here because nothing downstream reads the run's output
  mid-flight: step 2 parses the JSON the completed job returns, and the script
  owns its own locking and state.

- **`--stop-on-conflict`**: same as default but **pauses** (`tree: paused`, stash
  held) instead of aborting. Resume never aborts either: a further stop pauses,
  because aborting would discard resolutions already made.

`--no-push` composes with any of the above: the rebase runs (and the AI still
resolves conflicts under `--fix`), but nothing reaches the remote — the
force-push command is printed for the user to run instead.

When no branch argument is provided, omit `--branch` (uses CWD's branch).

JSON output is on stdout; status messages are on stderr.

### 1b. Reading a run in flight

Don't. The job messages you when it finishes, and the JSON that step 2 parses
does not exist until the run is over.

When the user asks what it is doing, or a run has gone long enough that you want
to know whether it is progressing or wedged, read the trail — not the tail of
the job's output, which is the last status line and says nothing about rate:

```bash
otto-log list --script pr-rebase --since 1h      # find the invocation
otto-log show <invocation>                        # every decision, timed
otto-log show <invocation> --json                 # adds each event's data field
```

The run logs its target ref and why, the mode, whether it started fresh or
resumed, and a `resolve_file:<path>` span per conflicted file — a timed pair of
events, opened before the file is classified and closed when it is staged. That
pair is what answers "is this nearly done": the gap between them is the rate,
and an open span with no close is the file being worked on right now. The count
of commits still to replay rides in `data.remaining` on both kinds of `step`
event — the one that resolves conflicts and the one that skips an empty commit
— and the default rendering omits `data`, so that one needs `--json`. Either way
it is one call, and no files written into the worktree to hold output the job
facility already has.

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

Every conflict a run meets is resolved by that run: git's rerere cache is held
off for the whole rebase, so nothing is replayed from an earlier one and nothing
this run resolves is recorded for a later one. The cache is shared by every
worktree of a repo and is read by a plain `git rebase` too, so recording an
unreviewed AI resolution into it put that resolution in the path of every later
rebase, including one run by hand.

Report commits replayed and any conflicts resolved. When `files_stale` is
non-empty, those files were staged from the incoming side and never rebuilt —
either the repo's regeneration command failed, or it declares none and has no
conventional `generate` task. Say so and tell the user to regenerate them
manually; a repo that keeps landing in the second case wants a
`rebase.regenerate` entry in its `.workbench.yml`. Done.

**Exit 3 — conflicts a human must look at.** Parse the JSON. `aborted` and
`tree` say whether the replay was thrown away (`tree: clean`) or left paused
(`tree: paused`):

```json
{
  "status": "conflicts",
  "files": ["src/auth.py", "tests/test_auth.py"],
  "rebase_head": "abc1234",
  "rebase_head_subject": "fix: auth token refresh",
  "remaining_commits": 3,
  "aborted": true,
  "tree": "clean",
  "classes": {"src/auth.py": "ai_merge", "tests/test_auth.py": "ai_merge"},
  "files_stale": [],
  "pre_rebase_head": "def5678"
}
```

Runs that reach this, and the difference is `tree` plus what the console said:

- **Default mode.** The rebase was **aborted** (`aborted: true`, `tree: clean`,
  HEAD back at `pre_rebase_head`). Do not ask the user whether to continue: the
  request to rebase is the request to finish it. Report `files` and `classes` in
  one line, then immediately start `pr rebase --fix --branch <branch>` as a
  background job (see step 1). It begins a **fresh** rebase (there is nothing to
  resume). The task is not done until the branch is rebased and pushed.
- **`--stop-on-conflict`.** The tree is **paused** (`aborted: false`,
  `tree: paused`, stash held). Report `files` and `classes`. Resume with
  `pr rebase --fix --branch <branch>` if the user wants AI, or resolve by hand
  and `pr rebase --branch <branch>` (a resume never aborts).
- **`--fix` mode, where a file could not be resolved.** The AI's answer for that
  file would not parse twice over (missing resolution markers, surviving git
  conflict markers, or a merged file that no longer parses as its language —
  `gofmt -e`, `ast.parse`, `bash -n`, JSON/YAML/TOML), or the file is binary.
  The broken file is not written or staged. Everything else in the step is
  already resolved and staged, and every commit replayed so far is intact — the
  rebase is *paused*, not aborted, and `files_resolved` in `state.json` records
  what it kept. `files` names only what is still unresolved. Resolve those
  yourself (see *Resolving by hand* below), stage them, and finish with
  `pr rebase --fix --branch <branch>`, which resumes from where it stopped.
- **`--fix` mode, where the AI backend is unavailable.** The console says so
  directly ("Cannot resolve conflicts — AI backend unavailable.") before any
  file is even attempted. Resuming with `pr rebase --fix --branch <branch>`
  hits the same wall again with nothing changed. Do not stop there: resolve the
  conflicts yourself instead. If the tree is paused, resolve `files` by hand,
  stage them, and resume with `pr rebase --stop-on-conflict --branch <branch>`;
  if the default run aborted, start the replay with that same command so it
  pauses rather than calling the backend. Repeat — each resume pauses at the
  next conflicting commit — until it exits 0, then finish at step 3.
- **A resolution that throws away changes.** The console prints "This
  resolution throws away changes" and lists, per file, each change git had
  merged cleanly that the resolution no longer contains — the signature of a
  whole-file `git checkout --ours`/`--theirs`. `files` names those files. When
  the run made the resolution itself, the conflict has been put back
  (`git checkout -m`) and resuming retries it. When the resolution was staged by
  hand before the run, it is left as it was: restore the conflict with
  `git checkout -m -- <file>`, resolve it hunk by hunk keeping every listed
  change, and resume. `WORKBENCH_ALLOW_DROPPED_CHANGES=1` is the user's
  override, never yours — if a listed change genuinely cannot be kept, stop and
  show them the list.

**Resolving by hand.** Edit each conflicted file hunk by hunk, keeping both
sides' intent and every change git merged cleanly; under Claude Code use Bash
for the edits, per `git-operations.md` § Rebase, Cherry-Pick, Merge Conflicts.
Never take a side whole (see Constraints). Check the file still parses —
`gofmt -e`, `bash -n`, or `python3 -m py_compile` — then
`git -C <worktree> add <file>` and resume.

Exit 3 is never a stopping point for the agent. After a default-mode abort, run
`--fix`; after a `--fix` run that paused on unresolved files, resolve them by
hand and resume; with the AI backend down, resolve every conflict by hand under
`--stop-on-conflict`. The only exit 3 you hand back is one the user asked for
with `--stop-on-conflict`, or a dropped-changes refusal you cannot rebuild.

Never answer a **paused** exit 3 (`tree: paused`) with `pr rebase --abort`.
The rebase holds real work by this point; aborting throws away every resolution
the run made and every commit it replayed. Abort only when the user asks for it
having been told that. A default-mode abort already left the tree clean — do
not abort again.

While a rebase is paused this way, an auto-stash from the run that started it is
*held* rather than popped — popping into a conflicted index cannot work. The run
that finishes the rebase restores it. If the user abandons the rebase instead,
their uncommitted work comes back with `git stash pop` after the abort.

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
| `partially_landed` | `partially_landed` | Some of the branch's commits are already in the base and some are not |
| `conflicts_over_budget` | `conflicts_over_budget` | The rebase conflicted across more files than automatic resolution should attempt; it was aborted |
| `resolutions_over_budget` | `conflicts_over_budget` | The rebase would have spent more AI resolution calls than the budget allows — the same files conflicting in commit after commit; it was aborted |

`commits_ahead` is a count on the two git signals and `null` on `pr_merged`: the
tracker is asked before the branch is checked out, so there is no honest count
to report there. `pr_number` is set on `pr_merged` only. `remedy` is set on
`partially_landed` only, and is the flag to re-run with.

`partially_landed` is handled without asking — re-run with its `remedy`, as
below. Every other status is a stop: report `detail` and what to suggest, which
depends on `status`:

- `already_landed` — rebasing would replay landed work and force-push a branch
  the merge deleted. Suggest deleting the worktree and branch instead.
- `unrelated_history` — the branch descends from a different root, usually one
  left by a re-initialised repo. Rebasing would replay its entire history onto a
  base it has nothing in common with. Suggest cherry-picking the wanted commits
  onto a fresh branch instead.
- `partially_landed` — an earlier part of the branch already landed, usually
  because someone merged a prefix of it or amended it on the way in. Replaying
  the whole branch would reapply those commits on top of themselves. This is the
  one refusal with a real fix rather than an override: re-run immediately, in
  the background like any `--fix` run, with the flag the `remedy` field gives,
  which replays only what is left. Then handle that run's result from the top
  of step 2.

  ```bash
  pr rebase --fix --fork-point <sha from remedy> --branch <branch>
  ```

  Use `remedy` verbatim — do not pick a fork point yourself. Fall back to
  `--force` only if the user says the detection is wrong, and say what forcing
  will replay before they agree.
- `conflicts_over_budget` — a branch conflicting this widely, or this
  repeatedly, has usually had its work land in another shape. The rebase was
  already aborted, so the worktree is clean; suggest checking whether the work is
  still wanted before forcing it. When the signal is `resolutions_over_budget`
  the spread was narrow and the *depth* was not — the same handful of files
  conflicting in commit after commit, which is what a partially-landed branch
  looks like once the replay is under way. Check for a landed prefix before
  forcing.

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
  The hook output on stderr is the tail only. The whole of it is written to a
  file the run names on a `full output: <path>` line, and `otto-log show
  <invocation>` prints the same path under the failing event — read that file
  when the tail does not name the check. Re-running with `--fix` only repeats
  the AI recovery that already failed here.
- **`Recovery left uncommitted changes — not pushing`.** A push-recovery step left
  edits outside any commit, so the branch was deliberately left unpushed
  (`force_pushed` is `false`). Pre-push hooks validate the worktree rather than the
  commits, so pushing there would green-light a HEAD no hook saw. The user's own
  uncommitted work is stashed during a fresh rebase, so these paths are the
  recovery's. Read their diff: commit what fixes the failing check as its own
  `fix:`/`chore:` commit, restore what is noise with
  `git -C <worktree> checkout -- <path>`, then finish at step 3. On a resumed
  rebase (no stash), stop instead if a path also held uncommitted work from
  before the run.

### 3. Finish the push

A run that ends with the rebase complete and `force_pushed` false is finished
through the owner, once whatever blocked the push is committed:

```bash
pr rebase --branch <branch>
```

This is the same command as default mode, and it is not report-only here:
conflict handling is already done. A replay with nothing to resolve force-pushes
either way. So the run re-fetches, replays nothing unless the base moved again,
and pushes — which puts the pre-push hooks on the HEAD that is actually going
out. This is how the skill ends. Never run `git push --force-with-lease` yourself,
and never hand it to the user as the remaining step: `pr rebase` prints a
`Resume: git -C '<worktree>' push --force-with-lease` line when a push is refused,
and that hint is for a human at a terminal, not an instruction to you.

When a hook failure is not the branch's doing, push past it yourself. Re-run
step 3 once to rule out a flake. If the same check fails again and what it
names lies outside the branch's diff (`git -C <worktree> diff --stat
<base>...HEAD`), finish with `pr rebase --no-verify --branch <branch>` and say
which check you pushed past and why. A failure the branch did cause is fixed and
committed, never skipped. The same command honours a user who has said to push
past it. It is still the owner's push, under the lease the rebase recorded —
never hand anyone a raw `git push --no-verify`.

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
  command, because that is what the user asked for. The policy behind this is
  `git-operations.md` § Pushing
- A fresh rebase auto-stashes the worktree, untracked files included, and restores
  it afterwards; the pre-push hooks then validate the branch alone. A resumed
  rebase cannot stash (the index is mid-rebase), so uncommitted work is still
  present while its hooks run
- Never run `pr rebase --fix` in the foreground, and never under a timeout below
  90 minutes — a killed run leaves a partial rebase that the next run resumes
  without the JSON saying so
- Background it through the harness's job facility and nothing else. A detached
  `&`/`nohup` run reports no completion, which is what turns a finished rebase
  into a poll loop, and (`general.md` § Waiting on Background Work) forbids it
  under every harness
- Never redirect a run's output into the worktree to read back later
  (`> ignore/rebase.json`). The job facility returns stdout on completion and
  `otto-log show` holds the detail; files written for the agent's own benefit are
  litter, which is why `pr` stopped writing its trail into working trees
- Never preflight with `git fetch`, `rev-list`, or `gh pr view` — see step 0
- Never resolve a conflict in a hand-written file with a whole-file
  `git checkout --ours`/`--theirs`, or by copying either side's file in. It
  replaces every hunk, including the ones git merged cleanly, and the branch's
  edits vanish with no marker left behind. `pr rebase` refuses to continue past
  one, and so does the global `prepare-commit-msg` hook. Taking one side whole is
  right only for generated files, which `pr rebase` already does by itself
