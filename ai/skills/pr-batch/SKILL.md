---
name: pr-batch
description: "Sweep many open PRs with pr batch and turn the result into focused per-PR sessions. TRIGGER when: updating, rebasing, triaging or sweeping several open PRs, or running pr batch. SKIP: a single PR (use pr-rebase, pr-comments, ci-failures)."
source: otto-workbench/ai/skills/pr-batch/SKILL.md
invocation: "/pr-batch"
trigger: "Use when updating, rebasing, triaging or sweeping several open PRs, or running pr batch."
skip: "Do not use for a single PR (use pr-rebase, pr-comments, or ci-failures instead)."
---

# PR Batch

Sweeps the viewer's open PRs with `pr batch`: rebase, CI fix, comments, and
self-review, each as a drafted child. The batch is the only thing that
publishes. A run that needs a person exits 10 (`waiting`); `pr batch next`
lists why and the `pr batch resolve` that answers it; `pr batch resume`
continues. This skill adds no lifecycle of its own — it wraps the CLI.

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

`run` plans for itself. Default `--steps` is rebase,ci,comments,review, each
only where the plan says it is needed. Last line is `run_summary`. Exit 0 is
done or cancelled; 10 is waiting on decisions, not failure.

Omit review when the sweep should not spend on it:

```bash
pr batch run --checkout DIR --steps rebase,ci,comments
```

`--prs` limits keys; `--select KEY=STEPS` runs exactly those steps for one
PR. `--auto-publish STEPS` answers a clean publish when every drafted step is
listed and done. `--no-verify` skips the pre-push hook on publishes the batch
makes; persist it only when the user has already read a hook failure.

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

When the run waits, read `pr batch next` (or `pr batch status`). Each `next`
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
`--auto-publish` answers it). Force pushes the batch would make are printed
for the user with the full 40-char SHA and `--expect` — never invent a
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
state. `pr batch status` / `pr batch next` `--offline` skip GitHub; without
it they show GitHub's view of the run's repos. Trust `next` over memory of
what you posted. Do not clear closeout debt by editing state files.

---

## Closeout

An item that still owes `pr comments --finish --post` shows as
`closeout_owed` on a finished run's `next`. Run that command only when the
user wants the closeout posted. Never file a tracking issue unless the user
explicitly asks — not because a `track` action is listed, not because
deferred threads exist.

---

## Rules

- Never kill a live batch driver; `pr batch status RUN_ID` / `pr batch cancel`.
