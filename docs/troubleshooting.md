# Troubleshooting

Common issues and how to resolve them.

## "command not found: otto-workbench"

`~/.local/bin` is not in your `PATH`. This is added automatically by `otto-workbench install`, but requires a shell reload:

```bash
exec zsh
```

If it's still missing, check that `~/.zshrc` contains a `PATH` entry for `~/.local/bin`.

## "bash 4.3+ required"

macOS ships with bash 3.2 (due to licensing). Install a modern version:

```bash
brew install bash
```

The workbench scripts use `#!/usr/bin/env bash` to pick up Homebrew's version automatically.

## Files "skipped (real file exists)" during sync

`otto-workbench sync` never silently overwrites real files — it warns and skips them. This protects your manual edits.

To resolve: back up the blocking file, remove it, then re-sync:

```bash
mv ~/.config/zsh/config.d/some-file ~/.config/zsh/config.d/some-file.bak
otto-workbench sync
```

For interactive overwrite prompts (overwrite/backup/skip), use `otto-workbench install` instead.

## "tools.generated.md is out of date" (pre-push failure)

The pre-push hook detected that generated files don't match their sources. Regenerate and commit:

```bash
generate-tool-context
generate-git-rules
git add ai/guidelines/rules/tools.generated.md ai/guidelines/rules/git.generated.md
git commit -m "chore: regenerate tool context"
```

## "yq not found"

Several workbench scripts depend on `yq` for YAML processing:

```bash
brew install yq
```

## AI setup: "AI_COMMAND not configured"

The global Taskfile needs an AI tool configured. Run:

```bash
task --global ai:setup
```

This creates `~/.config/task/taskfile.env` and prompts you for `AI_COMMAND`, `GH_TOKEN`, and optionally `ANTHROPIC_API_KEY`.

## "Cannot connect to the Docker daemon"

Start your Docker runtime:

```bash
# If using Colima:
colima start

# If using OrbStack:
# Launch OrbStack from Applications
```

The workbench detects your active runtime from the `~/.docker/run/docker.sock` symlink target — it doesn't matter which runtime is installed, only which is running.

## Shell changes not taking effect

ZSH configuration is copied (not symlinked) to `~/.config/zsh/config.d/`. After pulling workbench updates:

```bash
otto-workbench sync   # re-copies config files
exec zsh              # reloads the shell
```

## Git hooks not running

Hooks need to be activated once per clone:

```bash
task dev:setup
```

This sets `core.hooksPath` to point to the workbench's [`git/hooks/`](../git/hooks/) directory.

## "refusing to commit with a placeholder identity"

The commit identity is a test value. Rejected names are `test`, `your name`, and `unknown` (case-insensitive); rejected emails are `test@…` and anything at `test.com`, `example.com`, `example.org`, or `localhost`. Only repos whose `origin` remote points at GitHub, GitLab, or Bitbucket are checked, so throwaway repos built by test suites are unaffected.

A test suite that ran `git config` against the wrong repo is the usual cause — and in a bare-repo-plus-worktrees layout, one polluted `.git/config` mis-attributes commits from every worktree until someone notices.

The hook prints the file each value came from. Remove them there:

```bash
git config --unset user.name     # add --global or --file <path> to match the origin shown
git var GIT_AUTHOR_IDENT         # confirm the identity that will be used
```

If the hook printed no config origins, the identity is coming from `GIT_AUTHOR_NAME` / `GIT_AUTHOR_EMAIL` in the environment instead.

Commits already made under the bad identity keep it — rewriting them requires `git filter-branch --env-filter` plus a force push.

## "another pr run already owns this worktree"

A second `pr` run refused to start because one is already in flight against the same worktree. The message names the holder:

```
✗ another pr run already owns this worktree: pr review --self --fix (pid 15461, started 2026-08-12T07:21:19+00:00)
```

Two runs on one worktree corrupt each other — they both read-modify-write `.workbench/state.json`, and with `--fix` they both edit and commit the same files. Wait for the holder to finish, or stop it with the printed `kill <pid>`.

`pr status` is read-only and never contends. `pr gc` does take the lock — it deletes the state directory, so it is not safe to run against a live run.

`claude-review`, `ci-check`, and `review-threads` take the same lock when you invoke them directly, so `claude-review --self --fix` is guarded too. Launched by `pr` they inherit `WORKBENCH_WORKTREE_LOCK` from it and pass through rather than deadlocking on their own parent's lock.

The lock is an advisory `flock` on `.workbench/run.lock`, so the kernel releases it whenever the holder exits — including `kill -9`. There is no stale lock to clear by hand; if the message names a pid that is gone, the next run will take the lock regardless.

## "`.workbench/state.json` is unreadable — discarding it"

The per-worktree PR state file did not parse — truncated by a killed write, hand-edited, or written by an older schema. Nothing in it is authoritative: every field is rebuilt by the command that wrote it, so `pr` commands carry on with no cached state rather than failing.

Any command that writes state — `pr ci`, `pr review`, `pr comments`, `pr rebase` — replaces the file on its next run, so the warning usually clears itself. To clear it deliberately:

```bash
pr gc
```

`pr status` and the status line stay blank until something rebuilds the file. There is nothing to recover by hand; the file is a cache, not a record.

## "merge conflict" in `~/.claude/settings.json`

The AI sync merges `settings.json` rather than overwriting. If you see unexpected values, re-sync:

```bash
otto-workbench ai sync
```

This preserves your additions while applying workbench defaults. If the file is corrupted, delete it and re-sync — the workbench will recreate it from its template.
