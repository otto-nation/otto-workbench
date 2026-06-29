# Git — Operations

## Worktree-First Development

- Never edit or write files directly on `main`, `master`, or any shared/protected branch — always create a worktree first
- For implementation work on any branch, prefer using a worktree to isolate changes from the working tree — this prevents accidental modifications to uncommitted work and keeps the primary checkout clean
- Read-only operations (searching, reading files, exploring code) do not require a worktree
- Use the `wt` CLI for worktree management (`wt switch -c <branch>`) — never use the built-in `EnterWorktree` tool or the `superpowers:using-git-worktrees` skill

## Worktree Naming

- Bare repos use `{{ repo_path }}/../{{ branch | sanitize }}` — worktrees live as peers of `main/` inside the repo container (e.g., `homelab/main/`, `homelab/isaac-feat-auth/`). The `../` is needed because `repo_path` points to the `.git` bare directory, not the parent
- Per-project overrides in `~/.config/worktrunk/config.toml` take precedence — don't change them
- The global default is managed by `otto-workbench sync git` — don't edit `~/.config/worktrunk/config.toml` directly

## Protected Branches

- Never remove the `main` (or default branch) worktree — use `wt remove` only on feature branches
- Never commit on `main` — create a feature branch first, even for one-line changes
- Never run `git push` while on `main` or targeting `main` — branch protection will reject it, so don't attempt it at all
- All changes reach `main` through a merged PR, no exceptions

## Pushing

- Never run `git push --force` or `git push --force-with-lease` — always print the exact command for the user to run and stop
- After a rebase, skip the regular push attempt entirely — print the force-push command immediately
- When a regular push fails because the branch diverged, print the force-push command for the user
- Never retry a failed push/fetch — diagnose instead (see Git Failure Debugging)

## Commit History

- Never use `git commit --amend` — always create a new commit. Amending a pushed commit causes force-push situations, and amending after a failed pre-commit hook silently modifies the previous commit instead of creating the intended one

## Cross-Worktree Safety

- Never run tests, builds, or git-mutating commands in a worktree other than the current session's — test harnesses create temporary git repos that can corrupt the target worktree's branch and commit state
- When applying changes to another worktree, only use `git apply` and file copies — then tell the user to run tests there themselves

## PR Creation

- Always use `task --global pr:create -- --no-issue --draft` to create a PR — `task pr:create` does not exist (no local target), omitting `-- --no-issue` blocks on an interactive issue-number prompt, and `--draft` ensures PRs go through review before being marked ready
- To supply a custom title and/or body: `task --global pr:create -- --no-issue --draft --title "feat: title" --body "description"` — when both are provided, AI generation is skipped entirely; when only one is provided, the other is still AI-generated. Use `--body-file /path/to/file` instead of `--body` for multi-line or complex content
- When the current working directory is not the target repo (e.g., running from a different worktree), pass `REPO_DIR`: `task --global REPO_DIR=/path/to/worktree pr:create -- --no-issue --draft` — without this, git commands run against the CWD repo instead of the intended one

## Scope Discipline

- Never bundle unrelated fixes into commits on a feature branch — if you spot an issue outside the branch's scope, create a separate branch for it

## Branch Freshness

- Before starting work or creating a PR on an existing branch, rebase onto `origin/main` and verify the diff (`git diff --stat origin/main...HEAD`) contains only your changes — reversions indicate a stale base
- If a rebase has conflicts, resolve them before writing new code — don't add commits on top of a stale base

## Branch Completion

- Never present a menu of completion options — always create a PR when implementation is complete (see PR Creation for the full invocation)
- When the user states a specific next step ("merge this", "push it", "just keep it"), execute that action directly
- Never re-ask after the user has already chosen — if a chosen action fails, debug the failure, don't fall back to an options menu

## Git Failure Debugging

When a git command fails (push, fetch, pull, clone), diagnose in this order:

1. **Hooks** — `git config core.hooksPath`, run the hook directly
2. **Config** — signing, credential helpers, push settings
3. **Connectivity** — `ssh -T git@github.com` or HTTPS auth; verify remote URL
4. **Ref state** — `git status`, upstream tracking, `git ls-remote origin <branch>`
5. **Surface** — report findings and the fix; if unresolved, ask the user to run the command with `!`

## Branch Analysis

When analyzing what a branch contains vs main, use exactly two commands:

1. `git log --oneline origin/main..origin/<branch>` — two dots for log (commits on branch not on main)
2. `git diff --stat origin/main...origin/<branch>` — three dots for diff (changes from merge-base only)

- Never use two-dot diff — it includes main's changes and produces false noise on stale branches
- If three-dot diff is still noisy, the branch needs a rebase — don't filter the noise, fix the source

## Rebase, Cherry-Pick, Merge Conflicts

- During interactive git operations (rebase, cherry-pick, merge conflict resolution), use only Bash commands — the Edit tool can corrupt git's index state and abort the operation
