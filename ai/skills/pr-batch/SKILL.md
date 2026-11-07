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
opt-in self-review, each as a drafted child. The batch is the only thing that
publishes. A default run never waits on a decision: it exits 0 and
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
review). Last line is `run_summary`. Exit 0 is done or cancelled; a default
run never exits 10. Read `<run-dir>/report.md`. Per PR, run its printed
commands, or re-run that PR with AI:
`pr batch run --checkout DIR --prs o/r#N --select o/r#N=rebase --with-ai=rebase`.

`--with-ai=rebase,ci,comments` restores today's AI argv. `--with-review` adds
review. `--prs` limits keys; `--select KEY=STEPS` runs exactly those steps for
one PR. `--auto-publish STEPS` keeps the attended v1 run. `--no-verify` skips
the pre-push hook on publishes the batch makes; persist it only when the user
has already read a hook failure.

Findings the report names: `conflict_handwritten` / `conflict_generated`,
`ci_failing`, `threads_need_human`, `ready_to_push`, `stack_wait_base`,
`dirty_worktree`, `failed_unknown`. `block`/`wait` → item `needs_session`;
`act` only → `done`.

---

## Cost control

- Prefer `--steps rebase,ci,comments` unless the user asked for review.
- `--select o/r#N=review` reviews one PR without reviewing the rest.
- `pr batch resume --steps rebase,ci,comments` **may only narrow** a waiting
  or interrupted run. Excluded pending steps become skipped; a later rebase
  commit does not re-queue a dropped review. Adding a step is refused
  (exit 2): start a new run to add one.
- `pr batch release RUN_ID PR` drops one finished or stuck PR and frees its
  locks; the worktree stays. `pr batch cancel RUN_ID` stops the whole run.

---

## Decisions

For `--auto-publish` runs and runs started before this version. When the run
waits, read `pr batch next` (or `pr batch status`). Each `next`
entry has `why`, `prep`, `worktree`, `actions`, and copy-paste
`pr batch resolve RUN_ID DECISION_ID --action A` commands. Apply one action
per decision; do not invent flags.

| Kind | Actions | Notes |
|---|---|---|
| `step_review` | `accept`, `retry`, `skip-step`, `undo`, `open-chat` | `undo` is rebase only (`git reset --hard` to the pre-rebase head). **`one_sided` → `undo`, never `accept`.** |
| `comment_item` | `settle-fixed`, `settle-addressed`, `settle-dismissed`, `reply`, `track`, `open-chat` | `settle-dismissed` needs `--reason`; `reply` needs `--body-file`. |
| `rebase_conflict` | `retry`, `abort`, `open-chat` | `retry` resumes a paused replay, or restarts the rebase when the tree was aborted clean (`tree: clean`); `abort` drops a paused replay, or just skips the rebase when the tree is already clean. |
| `rebase_refused` | `drop-pr`, `force`, `rebase-from-fork-point` | `force` needs payload `override`; `rebase-from-fork-point` needs a named fork point. |
| `dirty_worktree` | `retry`, `drop-pr`, `open-chat` | Payload names a stash command. |
| `failed` / `interrupted` | `retry`, `skip-step`, `drop-pr` | `failed` also has `force-publish` for `not_incorporated_remote` or `drafted_dropped`. |
| `publish` | `publish`, `discard` | `publish` is the lease push; `discard` marks the item done without pushing. |
| `open_findings` | `accept`, `open-chat` | Load-only from older runs; prefer `step_review`. |

`open-chat` is for a UI; the CLI leaves the decision open. After resolve,
`pr batch resume` continues. Queued resolve JSON is `applied: false` plus
`holder` — exit 0 is not "the decision closed".

---

## Stacks

A stacked child waits for its base item's publish. `resume` retries
`stacked_on` once that base is terminal, rebasing from `--fork-point`.
`rebase_refused` offers `rebase-from-fork-point`. Do not rebase a child onto
a base that is still open in the run.

---

## Push protocol

Nothing is pushed until a `publish` decision is answered (or
`--auto-publish` answers it). `pr batch report` / `<run-dir>/report.md`
prints the lease push with the full 40-char SHA and `--expect` — never invent a
short SHA, never force-push by hand without that lease.

On a **non-draft** PR, comment on the PR before a hand push. Drafts do not
need that comment.

Print the exact `pr push --expect SHA` or `pr rebase --push-only --expect SHA`
for the user to run when the batch left a force-push unapplied. Do not run a
force push yourself unless the user asked and the command carries `--expect`
and the full SHA.

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
