---
name: finishing-a-development-branch
description: "Complete a development branch — verify tests, self-review, then open a draft PR. TRIGGER when: implementation is complete and the work needs to reach main. SKIP: work still in progress; a branch whose PR is already open."
source: otto-workbench/ai/skills/finishing-a-development-branch/SKILL.md
invocation: "/finishing-a-development-branch"
trigger: "Use when implementation is complete and the work needs to reach main, or when the user asks to finish, wrap up, or ship a branch."
skip: "Do not use while work is still in progress, or on a branch whose PR is already open — prefer a follow-up PR."
---

<!-- Overrides superpowers:finishing-a-development-branch, which presents a
     three-option completion menu, offers a local merge into the base branch,
     and creates the PR with whatever forge CLI it finds. All three conflict
     with this machine: the rules forbid a completion menu, require every change
     to reach main through a merged PR, and pin PR creation to pr create.
     Its cleanup step also calls `git worktree remove`, which conflicts with
     git-operations.md § Worktree Cleanup.

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

     Written against superpowers v6.3.0, which validate-skills holds against
     the pin in ai/pi/settings.json. When bumping the pin, re-read the upstream
     skill and update that line — executing-plans and subagent-driven-development
     both hand off to it by name, and of the three shims this is the one whose
     callers encode real behaviour (executing-plans calls it a REQUIRED SUB-SKILL
     and names "verify tests, present options, execute choice"). -->

# Finishing a Development Branch

**Core principle:** green suite → self-review → draft PR. The work reaches
`main` through a merged PR, every time.

**Announce at start:** "I'm using the finishing-a-development-branch skill to
complete this work."

## Step 1: Verify Tests

Run the tests this change reaches, the way the repo's AGENTS.md and testing
rules say, in this worktree and never in another. Where the repo forbids local
whole-suite runs, the pre-push gate and CI cover the rest.

**If tests fail**, report the failures and stop. Nothing below happens on a red
suite.

## Step 2: Confirm the Branch Is Finished

Before continuing, check that nothing is still on your list
(`git-operations.md` § Branch Completion).

Confirm the branch contains only your work. Fetch first — a local `origin/main`
that has not moved since yesterday reads a stale base as clean:

```bash
git fetch origin
git log --oneline origin/main..HEAD
git diff --stat origin/main...HEAD
git status --porcelain -uall
```

Three dots for the diff. Reversions or unrelated files mean a stale base —
`git-operations.md` § Branch Freshness.

The first two commands see committed work only. `git status --porcelain -uall`
is what catches the file that exists nowhere else: a dirty tree passes the diff
check silently, the PR opens without it, and you report a URL believing
everything shipped. Commit it or say why it is being left.

Where the branch targets something other than `main`, substitute it throughout
and pass `--base <branch>` in Step 4.

## Step 3: Self-Review

Required before any PR:

```bash
pr review --self --fix
```

From another directory, add `--repo-dir /path/to/worktree`.

The review measures against the branch's real base, which it derives: the PR's
base where there is one, else the branch this one is stacked on, else the
trunk. On a stacked branch that means the parent's commits are not read as this
branch's own. Pass `--base <branch>` when the derivation picks wrong — a stale
branch parked between this one and its real parent is the case that does it.

`--fix` is part of the command; no `--push` here, because `pr create` pushes in
Step 4 (`self-review.md` § Before PR Creation).

Read the review from `~/.local/state/workbench/reviews/`, present what the fix
pass did, and work through whatever it left open as your human partner directs
(`self-review.md` § Before PR Creation). Before opening the PR, check
`git rev-parse HEAD` against the review's `covered_sha`, else its `head_sha`;
if it is neither, re-run (`self-review.md` § The Review Covers One Commit).

Skip only if your human partner explicitly says to.

## Step 4: Open the PR

The work is complete and reviewed, so open the PR. Do not present a menu of
completion options — the rules forbid it, and this is the path.

```bash
pr create --draft
```

- Closing an issue on merge: `pr create --draft --closes 941`
- Other flags (`--issue`, `--repo-dir`, `--title`/`--body-file`, `--dry-run`,
  `--base`): `git-operations.md` § PR Creation.

`--draft` is not optional: PRs go through review before being marked ready.

Report the PR URL.

On a detached HEAD there is no branch for `pr create` to push. Cut
the branch first with `git switch -c <username>/<ISSUE-or-type>/<description_in_snake_case>`,
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

After the PR merges, cleanup is `wt-cleanup`, run without asking
(`git-operations.md` § Worktree Cleanup). Plans and specs live in the
workspace, which cleanup does not touch.

Prefer a follow-up PR. If you must push before merge,
(`git-operations.md` § A Branch With an Open PR Is Shared) says when to comment,
and the push owes `pr review --self --fix --push`
(`self-review.md` § The Review Covers One Commit).

## Quick Reference

| Situation | Action |
|-----------|--------|
| Tests failing | Stop. Report failures |
| Something still on your list | Finish it now — do not open the PR yet. Leftover work is a follow-up PR, not an issue |
| Diff shows unrelated reversions | Stale base — `git-operations.md` § Branch Freshness |
| Uncommitted files in the tree | Commit them or say why not — the PR will not carry them |
| Branch targets something other than `main` | Substitute it, and pass `--base` in Step 4 |
| Detached HEAD | `git switch -c` a branch first — `pr create` has nothing to push otherwise |
| Review findings open after the fix pass | Work through them, then re-run the review |
| HEAD moved past the review's `covered_sha`/`head_sha` | Re-run `pr review --self --fix` before the PR |
| Ready to ship | `pr create --draft` |
| Human partner named a next step | Do that directly |
| PR open, more work needed | Follow-up PR by default; if pushing by hand, comment first |
| PR merged | `wt-cleanup` (`git-operations.md` § Worktree Cleanup) |

## Common Rationalizations

| Excuse | Reality |
|--------|---------|
| "I'll offer merge / PR / keep and let them choose" | The rules forbid a completion menu. Open the PR. |
| "It's a small change — merge it into main locally" | Every change reaches `main` through a merged PR, no exceptions. |
| "I'll open it ready, it's finished" | `--draft`. Ready is the reviewer's signal, not yours. |
| "`gh pr create` is right here" | `pr create` applies the template, issue linking, and assignee rules. |
| "The review passed earlier" | It covered one SHA and, at most, the fix commit it qualified. If HEAD moved past both — a hand commit included — re-run. |
| "I'll review first and fix what it finds myself" | That is the churn `--fix` exists to remove. Run it with the flag. |
| "I'll push the last fix quietly by hand" | A branch with an open PR is shared. Say what changed. |
| "They said drop it, so I'll delete the branch" | Show what would be lost and get the word `discard` back. It exists nowhere else. |
| "The worktree is done, I'll clean it up" | Only after the merge, and only `wt-cleanup` (`git-operations.md` § Worktree Cleanup). |
