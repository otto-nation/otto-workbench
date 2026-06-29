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
- After a rebase, skip the regular push attempt entirely — rebase rewrites history so a regular push will always be rejected, and pre-push hooks waste time on a push that cannot succeed. Print the force-push command immediately
- When a regular push fails because the branch diverged (without a preceding rebase), print the force-push command for the user

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
- Before creating a PR, rebase onto `origin/main` and verify the diff (`git diff --stat origin/main...HEAD`) contains only your changes — reversions of changelogs, manifests, or other files indicate a stale base
- When creating a new branch, always branch from `origin/main` (already covered in git.generated.md) — never from a local `main` that may be behind
- If a rebase has conflicts, resolve them before writing new code — don't add commits on top of a stale base

## Branch Completion

- Never present a menu of completion options ("What would you like to do? 1. Merge 2. PR 3. Keep 4. Discard") — always create a PR via `task --global pr:create -- --no-issue --draft` when implementation is complete (see PR Creation section for the full invocation)
- When the user states a specific next step ("merge this", "push it", "just keep it"), execute that action directly
- Never re-ask after the user has already chosen — if a chosen action fails, debug the failure, don't fall back to an options menu

## Git Failure Debugging

When a git command fails (push, fetch, pull, clone), do not retry. Diagnose in this order:

1. **Check hooks first** — `git config core.hooksPath` and list the relevant hook file (pre-push, pre-commit, etc.). Run the hook directly to see its output — hooks that fail silently are the most common cause of unexplained git errors
2. **Check git config** — signing requirements (`commit.gpgSign`, `gpg.format`, `gpg.program`), credential helpers, push configuration (`push.default`, `push.autoSetupRemote`)
3. **Check connectivity** — `ssh -T git@github.com` for SSH remotes, or test HTTPS auth. Verify the remote URL is correct with `git remote -v`
4. **Check ref state** — `git status`, upstream tracking (`git rev-parse --abbrev-ref @{upstream}`), whether the branch exists on the remote (`git ls-remote origin <branch>`)
5. **Surface the diagnosis** — report what you found and the specific fix. If you cannot determine the cause after these steps, tell the user what you checked and ask them to run the failing command manually with `!` so the raw output lands in the conversation

Never run the same push/fetch command more than once. If it failed, the second attempt will also fail — diagnose instead.

## Branch Analysis

When analyzing what a branch contains vs main, use exactly two commands:

1. `git log --oneline origin/main..origin/<branch>` — two dots for log (commits on branch not on main)
2. `git diff --stat origin/main...origin/<branch>` — three dots for diff (changes from merge-base only)

- Never use two-dot diff (`origin/main..branch`) — it compares tips directly, including all of main's changes not yet merged into the branch. On a stale branch this produces hundreds of irrelevant files
- Three-dot diff (`origin/main...branch`) automatically diffs from the merge-base, showing only the branch's own changes — no separate `merge-base` call or grep filtering needed
- If the output still looks noisy after using three dots, the branch likely needs a rebase — don't filter the noise, fix the source

## Rebase, Cherry-Pick, Merge Conflicts

- During interactive git operations (rebase, cherry-pick, merge conflict resolution), use only Bash commands — the Edit tool can corrupt git's index state and abort the operation
