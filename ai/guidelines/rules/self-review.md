# Self-Review

## Before PR Creation

Before creating a PR (via `task pr:create`, `gh pr create`, or any method):

1. Run `claude-review --self` on the current branch
2. Read the review file from `~/.config/workbench/reviews/` and present the findings summary
3. Work through findings as the user directs (or run `claude-review --self --fix` to auto-fix)
4. Only proceed to PR creation when the user is satisfied

Skip if the user explicitly requests it ("skip the review", "just create the PR").

## Session-Start

Check `~/.config/workbench/reviews/` for a self-review matching the current repo and branch:

1. Read it and check `<!-- head_sha: -->` against current HEAD (`git rev-parse HEAD`)
2. If HEAD matches: present unresolved findings summary (count of open `- [ ]` items by severity)
3. If HEAD has moved: note the review may be stale and offer to re-run
