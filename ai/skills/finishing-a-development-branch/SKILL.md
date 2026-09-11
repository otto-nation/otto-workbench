---
name: finishing-a-development-branch
description: "Complete a development branch — verify tests, self-review, then open a draft PR. TRIGGER when: implementation is complete and the work needs to reach main. SKIP: work still in progress; a branch whose PR is already open."
source: otto-workbench/ai/skills/finishing-a-development-branch/SKILL.md
invocation: "/finishing-a-development-branch"
trigger: "Use when implementation is complete and the work needs to reach main, or when the user asks to finish, wrap up, or ship a branch."
skip: "Do not use while work is still in progress, or on a branch whose PR is already open — push and comment on the PR instead."
---

<!-- Overrides superpowers:finishing-a-development-branch, which presents a
     three-option completion menu, offers a local merge into the base branch,
     and creates the PR with whatever forge CLI it finds. All three conflict
     with this machine: the rules forbid a completion menu, require every change
     to reach main through a merged PR, and pin PR creation to task pr:create.
     Its cleanup step also calls `git worktree remove`, which `wt remove` owns.

     This file wins by name collision: Pi ranks ~/.agents/skills (scope user,
     origin top-level) ahead of a package resource, so the upstream copy is
     dropped even though the Superpowers extension re-adds its skills directory
     through resources_discover.

     Upstream's callers describe this skill as the point that "presents the
     options" (executing-plans, subagent-driven-development). They are
     describing upstream's menu, not a contract this file breaks: Step 3's
     self-review findings summary is the human turn, and the draft PR of Step 4
     is the gate. A caller's residual findings still reach your human partner
     through that caller's own final message.

     Written against superpowers v6.3.0. When bumping the pin, re-read the
     upstream skill — executing-plans and subagent-driven-development both hand
     off to it by name. -->

# Finishing a Development Branch

**Core principle:** green suite → self-review → draft PR. The work reaches
`main` through a merged PR, every time.

**Announce at start:** "I'm using the finishing-a-development-branch skill to
complete this work."

## Step 1: Verify Tests

Run the project's full suite, in this worktree — never in another.

**If tests fail**, report the failures and stop. Nothing below happens on a red
suite.

## Step 2: Confirm the Branch Is Finished

Opening a PR is a claim that the branch is done, and it is acted on as such — a
reviewer reads it, marks it ready, and merges. Before continuing, check that
nothing is still on your list: a test you meant to add, a finding you meant to
fix, a TODO you left. Anything outstanding belongs in the branch now, or in a
follow-up PR after this one — not in a PR you open and then push to.

Confirm the branch contains only your work. Fetch first — a local `origin/main`
that has not moved since yesterday reads a stale base as clean:

```bash
git fetch origin
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
git status --porcelain -uall
```

Three dots for the diff. Reversions or unrelated files mean a stale base —
rebase onto `origin/main` before going further, and resolve any conflicts before
writing new code.

The first two commands see committed work only. `git status --porcelain -uall`
is what catches the file that exists nowhere else: a dirty tree passes the diff
check silently, the PR opens without it, and you report a URL believing
everything shipped. Commit it or say why it is being left.

Where the branch targets something other than `main`, substitute it throughout
and pass `--base <branch>` in Step 4.

## Step 3: Self-Review

Required before any PR:

```bash
pr review --self
```

From another directory, add `--repo-dir /path/to/worktree`.

Read the review from `~/.local/state/workbench/reviews/`, present the findings
summary, and work through them as your human partner directs. A review covers
exactly the SHA in its `<!-- head_sha: -->` — so if you commit fixes for what it
found, those fixes are themselves unreviewed. Re-run before opening the PR, and
check `head_sha` against `git rev-parse HEAD` to be sure.

Skip only if your human partner explicitly says to.

## Step 4: Open the PR

The work is complete and reviewed, so open the PR. Do not present a menu of
completion options — the rules forbid it, and this is the path.

```bash
task --global pr:create -- --no-issue --draft
```

- Linked to an issue: `task --global pr:create -- --issue ENG-123 --draft`
- From a different directory: add `REPO_DIR=/path/to/worktree` before
  `pr:create`
- Custom title or body: `--title` / `--body-file`. Supplying both skips AI
  generation

`--draft` is not optional: PRs go through review before being marked ready.
`-- --no-issue` or `--issue <ID>` is not optional either — without one the
command blocks on an interactive prompt.

Report the PR URL.

On a detached HEAD there is no branch for `task pr:create` to push — it reads
`git branch --show-current`, which is empty, and fails obscurely downstream. Cut
the branch first with `git switch -c <username>/<ISSUE-or-type>/<description>`,
then open the PR.

**If your human partner names a different next step** — "merge this", "push it",
"just keep it" — do that instead, directly. If it fails, debug the failure;
do not fall back to a menu.

Discarding is the exception. "Throw it away", "drop this branch", "get rid of
it" destroys work that exists nowhere else, so confirm before acting: show what
would be lost — `git log --oneline origin/main..HEAD` and `git status
--porcelain -uall` — and ask them to reply with the word `discard`. Anything
else is not confirmation.

## Step 5: Leave the Worktree in Place

The worktree stays. Your human partner iterates on PR feedback there, and the
branch is now shared — someone may be reading it, may have marked it ready, may
be merging it.

Cleanup happens later, after the PR merges, and it is `wt remove` that does it —
never `git worktree remove`, and never on `main` or the default branch. Your
human partner runs it when they are done with the tree; `wt-cleanup` sweeps
merged branches in bulk. If `wt remove` refuses, the tree is dirty: read what is
there before forcing it, since `ignore/plans/` scratch work is gitignored and
the PR carried none of it.

If you do have to push again before the PR merges:
- Say so on the PR, in a comment naming what changed and why. A silent push
  wastes the review already done
- Never push to a PR marked ready without commenting first — ready is the author
  declaring the branch finished, and a later push retracts that
- Re-run `pr review --self`; the earlier review said nothing about the new commit
- `cannot lock ref` on push usually means the branch merged and was deleted.
  Check `git log --oneline origin/main..HEAD` and open a follow-up PR for
  whatever did not land

## Quick Reference

| Situation | Action |
|-----------|--------|
| Tests failing | Stop. Report failures |
| Something still on your list | Finish it now, or file it as a follow-up — do not open the PR yet |
| Diff shows unrelated reversions | Stale base — rebase onto `origin/main` |
| Uncommitted files in the tree | Commit them or say why not — the PR will not carry them |
| Branch targets something other than `main` | Substitute it, and pass `--base` in Step 4 |
| Detached HEAD | `git switch -c` a branch first — `pr:create` has nothing to push otherwise |
| Review findings open | Work through them, then re-run the review |
| HEAD moved since the review | Re-run `pr review --self` before the PR |
| Ready to ship | `task --global pr:create -- --no-issue --draft` |
| Human partner named a next step | Do that directly |
| PR open, more work needed | Follow-up PR by default; if pushing, comment first |
| PR merged | `wt remove` — never `git worktree remove` |

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "I'll offer merge / PR / keep and let them choose" | The rules forbid a completion menu. Open the PR. |
| "It's a small change — merge it into main locally" | Every change reaches `main` through a merged PR, no exceptions. |
| "I'll open it ready, it's finished" | `--draft`. Ready is the reviewer's signal, not yours. |
| "`gh pr create` is right here" | `task pr:create` applies the template, issue linking, and assignee rules. |
| "The review passed earlier" | It covered one SHA. If HEAD moved — including for review fixes — re-run. |
| "I'll push the last fix quietly" | A branch with an open PR is shared. Say what changed. |
| "They said drop it, so I'll delete the branch" | Show what would be lost and get the word `discard` back. It exists nowhere else. |
| "The worktree is done, I'll clean it up" | `wt remove`, after the merge — and never the default branch's worktree. |
