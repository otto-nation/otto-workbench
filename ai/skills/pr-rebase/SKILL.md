---
name: pr-rebase
description: "Rebase onto the branch's base with generated-only conflict rebuild or abort; --fix for AI resolution. TRIGGER when: user asks to rebase a branch, update it onto its base, squash-and-rebase, resolve rebase conflicts, or says hand rebase / rebase by hand. SKIP: simple git pull --rebase with no conflicts; commit rewording."
source: otto-workbench/ai/skills/pr-rebase/SKILL.md
invocation: "/pr-rebase [branch] [--fix] [--no-push] [--stop-on-conflict] [--force] [--onto|--base <ref>] [--fork-point <ref>] [--no-verify]"
trigger: "Use when the user asks to rebase a branch, update it onto its base, squash-and-rebase, resolve rebase conflicts, or says hand rebase / rebase by hand."
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

A rebase request is this CLI even when delegated — `git-operations.md` § Rebase, Cherry-Pick, Merge Conflicts.

Run with `/pr-rebase` or `/pr-rebase <branch>`.

## Finish without asking

When a requested rebase is finished, and when to push, is `git-operations.md` § Pushing.
This skill owns the exits and what "clean" looks like in `pr rebase`
output. Drive every outcome below to a finished run — `--fix` from the start
unless the user asked for no AI, resolve what the AI could not, fix a failing
pre-push check, then step 3 — and report what you did. Do not stop to ask
whether to continue or whether to use AI.

Stop and hand back only where the next move is a decision the tooling exists to
leave to a person, and say exactly which one it is:

- an exit 4 — the branch already landed, shares no history with its base, or
  conflicts past the budget, so the only way on is `--force` or deleting the
  branch
- a resolution that would drop changes git merged cleanly, where you cannot
  rebuild them hunk by hunk or verify each is superseded
- a pre-push check failure the branch caused and that you cannot fix
- anything the user asked to withhold: `--no-push`, `--stop-on-conflict`, or a
  request only to see what conflicts — run default mode, report `files`, and stop

---

## Arguments

- `branch` (optional): Target branch to rebase. Passed as `--branch` to
  `pr rebase`, which resolves the worktree automatically — and creates one with
  `wt switch` from any checkout when the branch has none, so there is no need
  to make it first. `--abort` and `--push-only` never create a worktree: they
  continue work that must already be in a checkout. When omitted, the current
  branch is used.
- `--fix` (optional): Resolve conflicts with AI and rebase (force-pushes
  unless `--no-push`). Pass it on the first run unless the user asked for no AI:
  it calls the AI only for a conflict default mode would have aborted on, so a
  clean or generated-only replay costs the same either way, and a real conflict
  is resolved in one run instead of an abort and a second replay. Incompatible
  with `--stop-on-conflict`.
- `--stop-on-conflict` (optional): Pause on conflicts (stash held, tree
  dirty, exit 3) instead of aborting. Use when the user wants to inspect or
  finish the replay by hand. Default without `--fix` is abort, not pause.
- `--no-push` (optional): Do everything except push. Composes with `--fix`
  independently — under `--fix` the AI still resolves conflicts, and the
  force-push command is printed for the user to run.
- `--force` (optional): Rebase a branch the preflight refuses — one whose work
  already landed on its base, one sharing no history with it, or one conflicting
  past the file budget — and also replay a landed prefix in full rather than
  skipping it. Only pass it when the user has seen the exit-4 refusal and asked
  for the rebase anyway, or when they say a detected skip is wrong — never add
  it speculatively.
- `--onto <ref>` (also spelled `--base`, optional): Rebase onto this ref
  verbatim, overriding the PR base and the default branch. Only pass it when
  the user names a base; the resolved default is right otherwise.
- `--fork-point <ref>` (optional): Replay only the commits *after* `<ref>`,
  rather than the whole branch. This is git's `<upstream>` argument, which
  `--onto` alone cannot express — with one ref, git's `<newbase>` and
  `<upstream>` are the same and every commit since the merge base is replayed.
  Only pass it when the user names one; detection of a landed prefix is
  automatic, and the run warns listing every skipped commit. It changes which
  commits end up on the branch. Not git's boolean `--fork-point`, and it is
  ignored on a resumed rebase.
- `--no-verify` (optional): Force-push without running the pre-push hook. The
  same rule as `pr create --draft --no-verify`: only for a hook failure already read
  and judged a flake or not the branch's doing — you make that judgement
  yourself by the test in step 3 — or when the user asks. It also
  skips the AI repair of pre-push failures, since no hook output exists to
  repair. Never add it to get past a failure nobody has read.

---

## Steps

### 0. Do not preflight

Go straight to step 1. `pr rebase` fetches, resolves its own base, detects an
in-progress rebase, and refuses the branches step 2's exit 4 describes — and it
records each of those decisions with its reason, so the answers arrive in the
trail rather than costing a turn each up front. When conflicts are expected to
repeat on the same files, it may squash the replay itself (`rebase.squash`);
do not preflight the CLI's automatic squash, count commits, or inspect merge settings first. A squash the user asked for is `git-operations.md` § Rebase, Cherry-Pick, Merge Conflicts.
Running `git fetch`,
`rev-list --count`, or `gh pr view` before it asks the script's own questions in
a worse form: the answer you get is from a second fetch, a moment earlier, with
no bearing on what the run then decides.

### 1. Run pr rebase

- **`--fix` mode** is the run to start with — see below. Default mode is for a
  user who asked for no AI, and for finishing a push (step 3).

- **Default mode** (generated rebuild or abort; no AI):

  ```bash
  pr rebase --branch <branch>
  ```

  Generated-class files (`accept_theirs` with a declared `rebase.regenerate`,
  or lockfiles in `LOCKFILE_REGENERATORS`) rebuild and the rebase continues.
  `rebase.regenerate_paths` limits which generated files those commands cover;
  an unmatched path lands in `files_stale`. The conventional `mise run generate`
  fallback does **not** qualify here — it stays `--fix`-only. Any other class,
  a missing declaration, a failed rebuild, or `files_stale` aborts
  (`tree: clean`, exit 3). Foreground is fine: there is no AI call.

- **`--fix` mode** (AI resolution). The first run, unless the user asked for no
  AI; if a default-mode run exits 3, rerun with this without asking:

  ```bash
  pr rebase --fix --branch <branch>
  ```

  **Start this as a background job, not a foreground command** — the Bash tool's
  `run_in_background` under Claude Code, `job_start` under Pi. Use the harness's
  own job facility, never a detached `&` or `nohup`: a job it started messages you
  on completion, while a detached shell leaves you polling for an exit you are
  never told about. Give it 90 minutes.

  Ninety clears the longest run on record (p90 24 minutes, slowest 74). A timeout
  is not a clean retry: the kill leaves a partial rebase in the worktree that the
  next run resumes without the JSON saying so. Backgrounding is safe because
  nothing reads the run's output mid-flight.

- **`--stop-on-conflict`**: same as default but **pauses** (`tree: paused`, stash
  held) instead of aborting. Resume never aborts either: a further stop pauses,
  because aborting would discard resolutions already made.

`--no-push` composes with any of the above: the rebase runs (and the AI still
resolves conflicts under `--fix`), but nothing reaches the remote — the
force-push command is printed for the user to run instead.

When no branch argument is provided, omit `--branch` (uses CWD's branch).

JSON output is on stdout; status messages are on stderr.

### 1b. Reading a run in flight

Don't; the job messages you when it finishes. If you must know whether a long run
is progressing, read the trail, not the job's output tail:

```bash
otto-log list --script pr-rebase --since 1h   # find the invocation
otto-log show <invocation> --json             # timed events; data.remaining = commits left
```

A `resolve_file:<path>` span with no close is the file being worked on now.

### 2. Handle the result

**Exit 0 — success.** Parse the JSON output:

```json
{
  "status": "clean",
  "commits_replayed": 22,
  "conflicts_resolved": 2,
  "files_resolved": ["orc-lending/go.mod", "orc-lending/go.sum"],
  "files_stale": ["orc-lending/go.sum"],
  "force_pushed": true,
  "fork_point": "abc1234",
  "squashed_commits": 5
}
```

`commits_replayed` counts only commits replayed from the branch after any
squash — commits the push recovery adds (regeneration, check fixes) are
excluded. `squashed_commits` is how many original commits were folded into
one before that replay (`null` when the run did not squash — never `0`).
`conflicts_resolved` counts conflicted-file resolutions, matching
`files_resolved`.

`fork_point` is non-null when a landed prefix was skipped. A detected skip is
also announced on the console as `Skipping already-landed commit(s): …` —
report both the SHA and those subjects to the user; they are not in
`commits_replayed`. Report `squashed_commits` the same way when it is not
null.

Every conflict is resolved by the run itself: git's rerere cache is held off, so
no unreviewed resolution is recorded for a later rebase.

Report commits replayed and any conflicts resolved. When `files_stale` is
non-empty, those files were staged from the incoming side and never rebuilt —
either the repo's regeneration command failed, the path is outside
`rebase.regenerate_paths`, or it declares none and has no conventional
`generate` task. Regenerate them yourself — see *After the rebase* — then finish at
step 3. A repo that keeps landing in the second case wants a
`rebase.regenerate` / `rebase.regenerate_paths` entry in its `.workbench.yml`.

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
  change, and resume. Set `WORKBENCH_ALLOW_DROPPED_CHANGES=1` on the
  resume only when you hand-merged the file deliberately and checked each listed
  change is superseded (for example, both sides added the same function and you
  kept one design) — and name those changes in your report. Never set it to get
  past a refusal on an AI or earlier resolution you have not read.

**Resolving by hand.** Edit each conflicted file hunk by hunk, keeping both
sides' intent and every change git merged cleanly; under Claude Code use Bash
for the edits, per `git-operations.md` § Rebase, Cherry-Pick, Merge Conflicts.
Never take a side whole (see Constraints). Check the file still parses —
`gofmt -e`, `bash -n`, or `python3 -m py_compile` — then
`git -C <worktree> add <file>` and resume.

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
| `conflicts_over_budget` | `conflicts_over_budget` | The rebase conflicted across more files than automatic resolution should attempt; it was aborted |
| `resolutions_over_budget` | `conflicts_over_budget` | The rebase would have spent more AI resolution calls than the budget allows — the same files conflicting in commit after commit; it was aborted |

`commits_ahead` is a count on the two git signals and `null` on `pr_merged`: the
tracker is asked before the branch is checked out, so there is no honest count
to report there. `pr_number` is set on `pr_merged` only.

Every status is a stop: report `detail` and what to suggest, which depends on
`status`:

- `already_landed` — rebasing would replay landed work and force-push a branch
  the merge deleted. Suggest deleting the worktree and branch instead.
- `unrelated_history` — the branch descends from a different root, usually one
  left by a re-initialised repo. Rebasing would replay its entire history onto a
  base it has nothing in common with. Suggest cherry-picking the wanted commits
  onto a fresh branch instead.
- `conflicts_over_budget` — a branch conflicting this widely, or this
  repeatedly, has usually had its work land in another shape. The rebase was
  already aborted, so the worktree is clean; suggest checking whether the work is
  still wanted before forcing it. When the signal is `resolutions_over_budget`
  the spread was narrow and the *depth* was not — the same handful of files
  conflicting in commit after commit, which is what a landed prefix looks like
  once the replay is under way. Check whether part of the branch already landed
  before forcing.

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

### 2b. After the rebase: regenerate and fold

A replay can leave generated files at the incoming side (`files_stale`), or
miss what the combined code now needs (a main-side schema or proto change).
Before pushing, from the worktree and as a background job when it is slow:

1. Build and vet the touched module, resolving compile errors from hand-merged
   files — a clean replay can still be semantically stale.
2. Run the repo's generator (`mise run generate` where declared). Commit any
   output as its own `chore:` commit. A generator that fails for an environment
   reason (Docker unreachable) is a blocker to report, not a step to skip.
3. If the repo allows one migration per service per PR, fold any migration the
   generator emitted into the branch's existing one and delete the new file.

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
- Never preflight with `git fetch`, `rev-list`, or `gh pr view` — see step 0.
  The CLI may squash on its own when conflicts would repeat; the skill does no
  squash preflight either
- `rebase.squash` is `auto` (default), `always`, or `never`. `auto` squashes
  only in a squash-merge-only repo when the replay would conflict repeatedly
  on the same files and the branch has one author. Opt out with
  `otto-workbench config set rebase.squash never --project`
- Never resolve a conflict in a hand-written file with a whole-file
  `git checkout --ours`/`--theirs`, or by copying either side's file in. It
  replaces every hunk, including the ones git merged cleanly, and the branch's
  edits vanish with no marker left behind. `pr rebase` refuses to continue past
  one, and so does the global `prepare-commit-msg` hook. Taking one side whole is
  right only for generated files, which `pr rebase` already does by itself
