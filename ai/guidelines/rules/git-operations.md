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

- Before starting work on an existing worktree branch, rebase it onto `origin/main` — stale branches cause merge conflicts that grow with every commit to main
- Before creating a PR, rebase onto `origin/main` and verify the diff (`git diff origin/main..HEAD`) contains only your changes — reversions of changelogs, manifests, or other files indicate a stale base
- When creating a new branch, always branch from `origin/main` (already covered in git.generated.md) — never from a local `main` that may be behind
- If a rebase has conflicts, resolve them before writing new code — don't add commits on top of a stale base

## Avoid Compound `cd` Commands

- Never use `cd <path> && <command>` — compound commands starting with `cd` trigger an unsuppressible security prompt in Claude Code. Use these alternatives instead:
  - `git -C <path> ...` for git commands
  - `gh --repo <owner/repo> ...` or `gh api repos/<owner>/<repo>/...` for GitHub CLI (no directory needed for API calls)
  - Run the command directly with absolute paths when possible

## Avoid Env-Var Prefix Syntax

- Never prefix a command with `VAR=value command` — Claude Code's permission matcher sees `VAR=value` as the command name, triggering a prompt every time. Use tool-native alternatives:
  - `task --global REPO_DIR=/path ...` (go-task variable syntax, not `REPO_DIR=/path task ...`)
  - `mise -C /path run ...` (not `REPO_DIR=/path mise run ...`)
  - `otto-workbench --workbench-dir /path ...` (not `WORKBENCH_DIR=/path otto-workbench ...`)

## Avoid `find -exec`

- Never use `find ... -exec` — Claude Code blocks `-exec` even with `Bash(find:*)` allowed because `-exec` can run arbitrary commands. Use piped alternatives instead:
  - `find ... -print0 | xargs -0 grep ...` instead of `find ... -exec grep ... {} \;`
  - `find ... -print0 | xargs -0 <command>` for other commands
  - Both `find` and `xargs` are already auto-allowed

## Rebase, Cherry-Pick, Merge Conflicts

- During interactive git operations (rebase, cherry-pick, merge conflict resolution), use only Bash commands — the Edit tool can corrupt git's index state and abort the operation
