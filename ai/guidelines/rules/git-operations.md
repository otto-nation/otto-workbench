# Git — Operations

## Worktree-First Development

- Never edit or write files directly on `main`, `master`, or any shared/protected branch — always create a worktree first
- For implementation work on any branch, prefer using a worktree to isolate changes from the working tree — this prevents accidental modifications to uncommitted work and keeps the primary checkout clean
- Read-only operations (searching, reading files, exploring code) do not require a worktree
- Use the `wt` CLI for worktree management (`wt switch -c <branch>`) — never use the built-in `EnterWorktree` tool or the `superpowers:using-git-worktrees` skill

## Protected Branches

- Never remove the `main` (or default branch) worktree — use `wt remove` only on feature branches
- Never commit on `main` — create a feature branch first, even for one-line changes
- Never run `git push` while on `main` or targeting `main` — branch protection will reject it, so don't attempt it at all
- All changes reach `main` through a merged PR, no exceptions

## Cross-Worktree Safety

- Never run tests, builds, or git-mutating commands in a worktree other than the current session's — test harnesses create temporary git repos that can corrupt the target worktree's branch and commit state
- When applying changes to another worktree, only use `git apply` and file copies — then tell the user to run tests there themselves

## PR Creation

- Always use `task --global pr:create -- --no-issue` to create a PR — `task pr:create` does not exist (no local target), and omitting `-- --no-issue` blocks on an interactive issue-number prompt

## Rebase, Cherry-Pick, Merge Conflicts

- During interactive git operations (rebase, cherry-pick, merge conflict resolution), use only Bash commands — the Edit tool can corrupt git's index state and abort the operation
