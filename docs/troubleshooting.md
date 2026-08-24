---
title: Troubleshooting
description: Common issues and how to resolve them.
---

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

## "Stale — run bin/local/compose-docs and commit the result"

A `docs/*.md` no longer matches what its `docs/*.src.md` composes to — usually because a registry, skill, or component changed and the artifact was not recomposed. Recompose and commit:

```bash
bin/local/compose-docs
git add docs/
git commit -m "docs: recompose generated sections"
```

Never edit the composed `docs/*.md` directly; the edit is overwritten on the next compose. Edit the `.src.md` for prose, or the source data behind the include for a generated section.

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

## `ssh: connect to host github.com port 22: Connection refused`

Some networks — hotel and conference Wi-Fi, corporate egress filters, a few consumer ISPs — block or intermittently reset outbound TCP/22. Push and fetch fail; HTTPS traffic to GitHub keeps working, which is why `gh` commands succeed while git does not.

Confirm it is the port rather than your keys:

```bash
nc -z -G 5 github.com 22       # the blocked one
nc -z -G 5 ssh.github.com 443  # GitHub's alternate SSH endpoint
```

If the second succeeds and the first does not, opt in to the alternate endpoint:

```yaml
# ~/.config/workbench/config.yml
github:
  ssh_over_443: true
```

```bash
otto-workbench sync git
```

`step_github_ssh` writes a marker-delimited `Host github.com` block into `~/.ssh/config` ahead of the first `Host` block — `ssh` keeps the first value it reads for each keyword, so the block has to precede a catch-all `Host *` to take effect. With the key set it adds `Hostname ssh.github.com` and `Port 443`, and teaches `known_hosts` the `[ssh.github.com]:443` spelling of the GitHub keys the machine already trusts, copied from the existing `github.com` entries rather than scanned off the network. Port 443 is the same service behind the same host keys.

Nothing outside the markers is read or rewritten, and setting the key back to `false` (or removing it) takes the routing lines out on the next sync — the block itself stays, because it also carries the keepalive described below. Edit the block by hand and the next sync will put the managed text back — change the config key instead.

## A push that passed every gate never reached the remote

`git push` opens the connection to the remote *before* it runs `pre-push`, and sends the packfile only once the hook returns. The socket is therefore idle for as long as the gates take, and the workbench's pre-push runs `validate-all` plus both test suites — over five minutes on a developer machine. Left idle that long, GitHub closes the connection, and the push fails after every gate has already printed a tick:

```
→ Running tests... ✓ (1453 tests, 149s)
→ Running pytest... Connection to ssh.github.com closed by remote host.
✓ (4642 tests, 185s)
```

The managed `Host github.com` block sets `ServerAliveInterval 30` and `ServerAliveCountMax 10` on every machine, whatever the routing key says: a keepalive every 30 seconds keeps the connection from being judged idle, and ten unanswered ones — five minutes of silence — is what it takes for `ssh` to call it dead.

If a push failed this way before the block was in place, `otto-workbench sync git` installs it. To confirm a ref actually landed rather than trusting the ticks:

```bash
git ls-remote origin <branch>   # compare against git rev-parse HEAD
```

## "Refusing to branch from a stale 'main'"

`wt switch --create` runs a `fetch-default` pre-switch hook that `otto-workbench sync git` installs, and worktrunk aborts the switch when a pre-switch hook fails. The hook is [`git/bin/wt-fetch-default`](../git/bin/wt-fetch-default), whose one job is to bring the default branch up to date before the new branch is cut from it — `wt` bases a new branch on the *local* default branch ref, so a stale ref means a branch that starts life behind `origin`.

It moves that ref two different ways, and the message names which one failed:

- **`Could not fetch origin/main.`** — a worktree holds the default branch, but the fetch that precedes the fast-forward itself failed: a network outage, an auth problem, or `origin` being unreachable. Diagnose the fetch directly (see Git Failure Debugging) before retrying.
- **`could not fast-forward to origin/main`, with a worktree path** — a worktree holds the default branch and would not fast-forward. Either it carries commits `origin` does not have, or uncommitted changes conflict with what is coming in. Deal with them in that worktree.
- **`Could not fast-forward 'main' to origin/main`, with no path** — no worktree holds the branch, so the ref was moved directly with `git fetch origin main:main`, and git refused because the two have diverged. Inspect with `git log --oneline origin/main..main` and reset the local branch once you are satisfied nothing is lost.

`wt switch --create <branch> --no-hooks` skips the check when you already know the base is fine. Prefer fixing the default branch: the abort exists because a branch cut from a stale base surfaces much later as a three-dot diff full of reversions.

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

## "Cannot determine repository via `gh repo view`"

A `pr`, `claude-review`, `ci-check`, or `review-threads` run could not name the repository it was pointed at. The message quotes what `gh` itself said rather than guessing at a cause, so the rest of the line is the diagnosis:

```
✗ Cannot determine repository via `gh repo view`: no git remotes found
✗ Cannot determine repository via `gh repo view`: gh: Bad credentials (HTTP 401)
✗ Cannot determine repository via `gh repo view` — server error, retry later: HTTP 503: No server is currently available to service your request. (https://api.github.com/graphql)
```

A 5xx is called out separately because it is the one case where nothing local is wrong: GitHub is down or degraded (check <https://www.githubstatus.com>), and the fix is to wait rather than to touch the remote, the token, or the worktree. Anything else — no remote, a repo `gh` cannot see, an expired token — is local and named by the command's own text.

Run the same command by hand to see the untruncated output:

```bash
gh repo view --json nameWithOwner -q .nameWithOwner
```

The same treatment applies to every message in `ai/lib/pr_context.py` that reports a *command* failing — `Cannot determine current branch`, `` `gh pr view` could not read the head of <repo>#<n> ``, `git reset --hard origin/<branch> failed`, `resolve-branch could not resolve <hint>`, and the `wt switch` failures all quote the underlying command's stderr through one helper, `pr_context.failure_message()`. If one of them ever prints a bare action with no cause, the command wrote nothing to stderr; re-run it by hand.

Messages that report the *repository's shape* rather than a command's exit — `Not in a git repository`, `Bare repository — pass --branch or --repo-dir`, `Cannot read the origin remote`, `No worktree for <branch>` — have nothing to quote: the condition is what the code checked, not what a subprocess said, so the message already names the whole cause.

## "another pr run already owns this target"

A second `pr` run refused to start because one is already in flight against the same target — the same `(origin repo, branch)`, regardless of which directory either run was launched from. The message names the holder:

```
✗ another pr run already owns this target: pr review --self --fix (pid 15461, started 2026-08-12T07:21:19+00:00)
```

Two runs against one PR corrupt each other — they both read-modify-write that target's `state.json`, and with `--fix` they both commit to the same branch, whether from one checkout or two. The lock is keyed on what a run targets, not where it was launched: reviews of two different PRs from one directory run concurrently, and two runs against the same PR exclude each other from anywhere. Wait for the holder to finish, or stop it with the printed `kill <pid>`.

`pr status` is read-only and never contends — it takes no lock, makes no network call, and creates nothing, so it answers while a review holds the target, and it answers with `gh` logged out. See [what each `pr` command needs before it runs](ai-libraries.md#pr_contextpy). `pr gc` prunes target state for merged and closed PRs, skips its own target, and takes each target's lock before touching it — so it will not delete state out from under a running review, and it is safe to run at any time.

`claude-review`, `ci-check`, and `review-threads` take the same lock when you invoke them directly, so `claude-review --self --fix` is guarded too. Launched by `pr` they inherit `WORKBENCH_RUN_LOCK` from it and pass through rather than deadlocking on their own parent's lock. Those three are the whole list — `pr-rebase` and `pr-describe` take no lock of their own, so run them as `pr rebase` and `pr describe` if you want them serialized.

The lock is an advisory `flock` on the target's `run.lock`, so the kernel releases it whenever the holder exits — including `kill -9`. There is no stale lock to clear by hand; if the message names a pid that is gone, the next run will take the lock regardless.

Both files live outside every checkout, in the target's own directory: `~/.config/workbench/pr/<repo-key>-<branch-slug>/` (rooted at `WORKBENCH_STATE_DIR` when you set it). The two components come from `git remote get-url origin` and the branch, so every worktree of one PR resolves the same directory — that is what lets the lock reach across checkouts. Nothing is written into the working tree, so there is no `.gitignore` entry to maintain, and `wt remove` leaves the target's state alone; `pr gc` prunes it once the PR is merged or closed.

## "`state.json` is unreadable — discarding it"

The run target's PR state file did not parse — truncated by a killed write, hand-edited, or written by an older schema. Nothing in it is authoritative: every field is rebuilt by the command that wrote it, so `pr` commands carry on with no cached state rather than failing.

Any command that writes state — `pr ci`, `pr review`, `pr comments`, `pr rebase` — replaces the file on its next run, so the warning usually clears itself. To clear it deliberately:

```bash
pr gc
```

`pr status` and the status line stay blank until something rebuilds the file. There is nothing to recover by hand; the file is a cache, not a record.

A file that parses but holds a wrong-typed value is handled without the warning: a field whose value cannot be read as its recorded type falls back to its default, so a hand-edit that leaves `"many"` where a count belongs costs you that one field rather than the file. The next write restores it.

## "merge conflict" in `~/.claude/settings.json`

The AI sync merges `settings.json` rather than overwriting. If you see unexpected values, re-sync:

```bash
otto-workbench ai sync
```

This preserves your additions while applying workbench defaults. If the file is corrupted, delete it and re-sync — the workbench will recreate it from its template.
