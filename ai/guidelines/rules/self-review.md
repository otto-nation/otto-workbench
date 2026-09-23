# Self-Review

## Before PR Creation

Before creating a PR (via `task pr:create`, `gh pr create`, or any method):

1. Run `pr review --self --fix` on the current branch
2. Read the review file from `~/.local/state/workbench/reviews/` and present what the fix
   pass did — findings fixed, findings left open, and the reason each was skipped
3. Work through whatever it left open as the user directs
4. Only proceed to PR creation when the user is satisfied

Do not act on a finding before step 2. The fix pass is working the same list you are,
and a finding you fix or file while it runs is duplicated work at best — at worst you
file an issue for something the pass is committing as you write it. Read what it did
first, then act on the remainder.

Read what it *did* from the diff, not from what it said. See § The Summary Is a Claim
About the Tree below — a finding the summary reports as skipped may be one the same
commit changed the code for.

`--fix` is part of the command, not an upgrade to it. Reviewing without it produces a
findings list somebody then has to apply by hand — a slower, sloppier version of the pass
the fix agent would have run, and a second round trip before the branch is shippable. It
costs an extra agent pass, and that is the trade: the bare `pr review --self` is right only
when you want the findings *without* the edits — sizing up a branch you are not about to
ship, or reading what a review says before deciding whether to act on it.

Add `--push` only when the branch already has an open PR, so the fix commit reaches the
branch someone is reading. Before the PR exists, `task pr:create` does the pushing and the
local commit is enough. `--push` requires `--fix`, which in turn requires `--self`.

The `self-review-fix` skill is the one documented exception: it always passes `--push`,
because it has no promise that `task pr:create` runs immediately afterward, and leaving a
gap between the fix commit and its push is the same risk this rule exists to close. Follow
the conditional form above for a manual invocation that PR-creates itself right after.

When running from a different directory than the target repo, use `--repo-dir`:
`pr review --self --fix --repo-dir /path/to/worktree` — the flag is `--repo-dir`, not
`--repo`.

Skip if the user explicitly requests it ("skip the review", "just create the PR").

## The Summary Is a Claim About the Tree

A fix pass's commit message and terminal summary are rendered from the agent's own boxes
on the tracking file. The commit is not: staging takes every path the pass touched, with
the outcomes unread. Attribution between the two is by path, and an agent that answers a
finding by editing its caller or its test moves a file no check can tie back to it.

So `Skipped: [M1] no auto-fix` could sit on top of a commit containing the edit for M1,
by three routes. Two now report themselves; the third cannot:

| What the agent did | What happens now |
|---|---|
| Ticked `needs a person` and changed the code anyway | Contradicted and sent to the gate, as a deferral already was |
| Deferred where the anchor file did move, gate reached no verdict | Says so in the row, instead of falling back to `no auto-fix` |
| Edited a caller or a test, deferred the finding | **Unreported by outcome.** A pass claiming no fixes at all lists the files it is committing; a pass with one real fix among them does not |

The third is the path-attribution ceiling and is not closeable by a check: a file moving
is evidence that something happened, never that *this item* is what happened. That is
what this manual step is for.

A change reaching main this way is the worst case — not unreviewed, but *reported as
absent*, so nobody looks. A regression shipped this way is invisible to the one artifact
everyone reads afterwards.

Therefore, at step 2 above, audit the diff:

1. `git show --stat HEAD` for what the pass actually touched
2. Read the hunks against the findings list, not against the summary
3. Any finding reported skipped or declined whose code moved is unreviewed work — review
   it now, or revert that hunk. Do not take the annotation as a statement that nothing
   happened

A row reading "the gate reached no verdict" or a footer naming files no fix claims is the
pass telling you where to start. Neither is a finding against the agent: both mean the
evidence is in the diff and nowhere else.

This is the same failure as a masked exit status (`testing.md` § Reading a Suite Result):
an artifact that reports on work is not the work, and the report is the thing that can
quietly be wrong while the work is fine — or the reverse.

## Three Rounds Means the Review Is Not Seeing the Defect

A review round answers the findings in front of it, and a fix round answers them one at a
time. Neither is shown the subsystem whole. When round after round keeps producing
findings in the same lifecycle, the loop has stopped converging on a defect and started
walking around one.

The signal that it has gone wrong is a round that *reinstates* what an earlier round
removed. A fix pass on a branch whose entire purpose was removing a hang on interrupt
reintroduced the identical hang one level up, in the context manager wrapping the code it
was editing — a correct answer to the finding it was given, and a regression of the
branch.

So after the third round on one subsystem, stop taking findings and audit the whole
thing yourself: every path into it, every path out, and every teardown. Both times this
has been done here it surfaced defects no round had reached. Feed the result back as a
single change rather than as round four — the loop's failure mode is incrementalism, and
one more increment is not the fix for it.

## The Review Covers One Commit

A review is written against the SHA in its `<!-- head_sha: -->`, and says nothing about
anything committed after it. Two commits pushed after a passing review are two commits that
reached the merge unreviewed, however green the review file looks.

1. Before creating a PR, check the review's `head_sha` against `git rev-parse HEAD`. If HEAD
   has moved, re-run the review with `--fix` — do not open the PR on the strength of a
   review of an earlier commit
2. The same applies to a commit that only fixes review findings. Fixing what a review found
   changes the code the review was written against, so the fixes are themselves unreviewed.
   Re-run with `--fix` before creating the PR
3. If the branch already has an open PR and you have pushed to it, re-run the review with
   `--fix --push` (see Before PR Creation above) and say on the PR what changed. See
   `git-operations.md` § A Branch With an Open PR Is Shared

Re-running is cheap and finds real defects: a second pass over a branch whose first pass was
clean has caught an injection left open by caller discipline, a docstring overclaiming what
its tests covered, and duplicated test logic across two suites.

## Session-Start

Check `~/.local/state/workbench/reviews/` for a self-review matching the current repo and branch:

1. Read it and check `<!-- head_sha: -->` against current HEAD (`git rev-parse HEAD`)
2. If HEAD matches: present unresolved findings summary (count of open `- [ ]` items by severity)
3. If HEAD has moved: note the review may be stale and offer to re-run with `--fix`
