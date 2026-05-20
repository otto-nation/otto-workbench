# Self-Review

## Before PR Creation

Before creating a PR (via `task pr:create`, `gh pr create`, or any method):

1. Run `claude-review --self` on the current branch
2. Read `ignore/reviews/self-review.md` and present the findings summary
3. Work through findings as the user directs
4. Only proceed to PR creation when the user is satisfied
5. After creating the PR, delete `ignore/reviews/self-review.md`

Skip if the user explicitly requests it ("skip the review", "just create the PR").

## Session-Start

If `ignore/reviews/self-review.md` exists in the current worktree:

1. Read it and check `<!-- head_sha: -->` against current HEAD (`git rev-parse HEAD`)
2. If HEAD matches: present unresolved findings summary (count of open `- [ ]` items by severity)
3. If HEAD has moved: note the review may be stale and offer to re-run
