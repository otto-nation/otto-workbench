# Self-Review

## Before PR Creation

Before creating a PR (via `task pr:create`, `gh pr create`, or any method):

1. Run `pr review --self --fix` on the current branch
2. Read the review file from `~/.local/state/workbench/reviews/` and present what the fix
   pass did — findings fixed, findings left open, and the reason each was skipped
3. Work through whatever it left open as the user directs
4. Only proceed to PR creation when the user is satisfied

`--fix` is part of the command, not an upgrade to it. Reviewing without it produces a
findings list somebody then has to apply by hand — a slower, sloppier version of the pass
the fix agent would have run, and a second round trip before the branch is shippable. It
costs an extra agent pass, and that is the trade: the bare `pr review --self` is right only
when you want the findings *without* the edits — sizing up a branch you are not about to
ship, or reading what a review says before deciding whether to act on it.

Add `--push` only when the branch already has an open PR, so the fix commit reaches the
branch someone is reading. Before the PR exists, `task pr:create` does the pushing and the
local commit is enough. `--push` requires `--fix`, which in turn requires `--self`.

When running from a different directory than the target repo, use `--repo-dir`:
`pr review --self --fix --repo-dir /path/to/worktree` — the flag is `--repo-dir`, not
`--repo`.

Skip if the user explicitly requests it ("skip the review", "just create the PR").

## The Review Covers One Commit

A review is written against the SHA in its `<!-- head_sha: -->`, and says nothing about
anything committed after it. Two commits pushed after a passing review are two commits that
reached the merge unreviewed, however green the review file looks.

1. Before creating a PR, check the review's `head_sha` against `git rev-parse HEAD`. If HEAD
   has moved, re-run the review — do not open the PR on the strength of a review of an
   earlier commit
2. The same applies to a commit that only fixes review findings. Fixing what a review found
   changes the code the review was written against, so the fixes are themselves unreviewed.
   Re-run before creating the PR
3. If the branch already has an open PR and you have pushed to it, re-run the review and say
   on the PR what changed. See `git-operations.md` § A Branch With an Open PR Is Shared

Re-running is cheap and finds real defects: a second pass over a branch whose first pass was
clean has caught an injection left open by caller discipline, a docstring overclaiming what
its tests covered, and duplicated test logic across two suites.

## Session-Start

Check `~/.local/state/workbench/reviews/` for a self-review matching the current repo and branch:

1. Read it and check `<!-- head_sha: -->` against current HEAD (`git rev-parse HEAD`)
2. If HEAD matches: present unresolved findings summary (count of open `- [ ]` items by severity)
3. If HEAD has moved: note the review may be stale and offer to re-run with `--fix`
