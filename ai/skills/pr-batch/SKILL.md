---
name: pr-batch
description: "Sweep many open PRs with pr batch and turn the result into focused per-PR sessions. TRIGGER when: updating, rebasing, triaging or sweeping several open PRs, or running pr batch. SKIP: a single PR (use pr-rebase, pr-comments, ci-failures)."
source: otto-workbench/ai/skills/pr-batch/SKILL.md
invocation: "/pr-batch"
trigger: "Use when updating, rebasing, triaging or sweeping several open PRs, or running pr batch."
skip: "Do not use for a single PR (use pr-rebase, pr-comments, or ci-failures instead)."
---

# PR Batch

Sweeps the viewer's open PRs with `pr batch`: rebase, CI, comments, and
opt-in self-review, each as a drafted child. Nothing is pushed unless
`--auto-publish ff`. A default run never waits on a decision: it exits 0 and
`pr batch report` / `<run-dir>/report.md` prints what needs a person.
This skill adds no lifecycle of its own — it wraps the CLI.

Run with `/pr-batch`.

---

## When to use

Several open PRs need updating, rebasing, CI, comments, or self-review in one
pass. For one PR, use `pr-rebase`, `pr-comments`, or `ci-failures` instead.

---

## Preflight

Do these before `pr batch run`. They are not automatic.

1. `otto-workbench config get github.owners` — GitHub writes refuse when
   owners are unset. Record owners before the run, not after a child fails.
2. `otto-workbench config get fix.verify_command` — a missing command leaves
   every CI/comments fix unverified. Set it on the target repos that will run
   those steps.
3. `git worktree list` — a worktree already sitting on a branch the batch
   will assign collides. Move it (`git worktree move`) or leave that PR out
   with `--prs`.
4. `mise trust` on each checkout the run will enter, when the repo uses mise.
5. `pr batch plan --checkout DIR` — read the matrix; note stacks (a child
   whose base is another open PR). Build a stack map from `base_ref` /
   `stacked_on` before starting.

---

## Run

Start as a background job; it streams NDJSON and can run a long time:

```bash
pr batch run --checkout DIR
```

`run` plans for itself. Default `--steps` is rebase,ci,comments (no AI, no
review). Last line is `run_summary`. Exit 0 is done or cancelled. Read
`<run-dir>/report.md`. Per PR, run its printed commands, or re-run that PR
with AI:
`pr batch run --checkout DIR --prs o/r#N --select o/r#N=rebase --with-ai=rebase`.

`--with-ai=rebase,ci,comments` restores today's AI argv. `--with-review` adds
review. `--prs` limits keys; `--select KEY=STEPS` runs exactly those steps for
one PR. `--auto-publish ff` (fast-forward only). `--no-verify` skips
the pre-push hook on publishes the batch makes; persist it only when the user
has already read a hook failure.

---

## Cost control

- Prefer `--steps rebase,ci,comments` unless the user asked for review.
- `--select o/r#N=review` reviews one PR without reviewing the rest.
- `pr batch resume --steps rebase,ci,comments` **may only narrow** an
  interrupted run. Excluded pending steps become skipped; a later rebase
  commit does not re-queue a dropped review. Adding a step is refused
  (exit 2): start a new run to add one.
- `pr batch release RUN_ID PR` drops one non-terminal PR and frees its
  locks; the worktree stays. Terminal items are refused. `pr batch cancel RUN_ID`
  stops the whole run.

---

## Findings

Read `pr batch report` / `report.md`. Printed commands come from the report;
do not invent flags. Never accept a stale rebase. Some findings have no
command: read the log or wait.

| Finding | Meaning | Command |
|---|---|---|
| `conflict_handwritten` | rebase aborted on a hand-written conflict | `pr rebase --fix --no-push --repo-dir WT` |
| `conflict_generated` | rebase aborted; generated files need a declared regenerator | `pr rebase --fix --no-push --repo-dir WT` |
| `stale_generated` | rebase left generated files stale | `git reset --hard <pre_rebase_head>` — never accept |
| `ci_failing` | read-only CI is red | `pr ci --fix --repo-dir WT` |
| `review_findings` | open self-review findings | `pr review --self --fix --repo-dir WT` |
| `threads_need_human` | review comments need a person | `pr comments --fix --repo-dir WT` |
| `closeout_owed` | `pr` still owes a closeout | `pr comments --finish --post --repo-dir WT` |
| `ready_to_push` | local work is ready under the lease | printed `pr push --expect` / `pr rebase --push-only --expect` |
| `wrong_branch` | worktree is on another branch | `git -C WT switch <branch>` |
| `push_refused` | `publish.plan` refused | rewritten commits only: the lease push (§ Push protocol); otherwise read the refusal |
| `stack_wait_base` | base PR is still open | none — wait for it to merge |
| `dirty_worktree` | paused rebase or dirty tree | none — stash, or `pr rebase --abort` when paused |
| `worktree_collision` | another worktree occupies the path | none — `git worktree move` |
| `budget_exhausted` | AI quota or spend limit | none — wait for it to reset |
| `failed_unknown` | unclassified failure | none — read the log path |

`block`/`wait` → item `needs_session`; `act` only → `done`.

---

## Stacks

`stack_wait_base` waits for the base PR to merge. Then rebase the child with
`--fork-point`. Do not rebase a child onto a base that is still open.

---

## Push protocol

When to push without asking is `git-operations.md` § Pushing; whether to comment
on the PR is § A Branch With an Open PR Is Shared. This section only maps them
onto batch state.

Nothing is pushed unless `--auto-publish ff`, which never force-pushes, so a
rebase always ends with the push still owed:

- **The lease** — the item's full 40-char `remote_sha` from
  `pr batch status --full`. Never a short SHA, never a push without `--expect`.
- **Clean** — the item is `done`. A `push_refused` naming only the commits the
  rebase rewrote is the expected refusal for a replay, not a blocker.
- **Not clean** — the item is `needs_session` (`stale_generated`, a conflict, a
  dirty tree). Fix and commit in its worktree first.
- **The push** — `pr rebase --push-only --expect SHA --repo-dir WT`.

---

## Hand-work reconciliation

Work done in the GitHub UI or another machine does not always match `pr`
state. `pr batch report --offline` skips GitHub; without it the report may
read GitHub. Trust `report.md` over memory of what you posted. Do not clear
closeout debt by editing state files.

---

## Closeout

An item that still owes closeout shows as `closeout_owed` in
`pr batch report` / `report.md`. The printed command is `pr comments --finish --post`
(it no longer files). Run a filing command (`--track <id>`, never `--track-all`)
only when the user explicitly asks. Never file because deferred threads exist.

---

## Rules

- Never kill a live batch driver; `pr batch status RUN_ID` / `pr batch cancel`.
