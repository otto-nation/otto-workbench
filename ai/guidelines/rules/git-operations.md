# Git — Operations

## Procedures live in the git-workflow skill

Load the `git-workflow` skill before opening, describing or pushing to a PR, rebasing
or resolving conflicts, replying to review comments, starting work on an existing
branch, finishing a branch, or debugging a failed git command. It holds PR Creation,
The PR Template, Replying to Review Comments, Branch Freshness, Branch Completion,
A Branch With an Open PR Is Shared, Git Failure Debugging, Branch Analysis, and
Rebase, Cherry-Pick, Merge Conflicts. The rules below are the ones that hold always.

- During an interactive rebase, cherry-pick or merge conflict use only Bash, never the Edit tool (`git-workflow/SKILL.md` § Rebase, Cherry-Pick, Merge Conflicts)
- Never resolve a conflict in a hand-written file with a whole-file `git checkout --ours`/`--theirs` (same section)
- Answer review threads only through `pr comments`, never a bulk-reply tool, raw `gh api` or manual resolve (`git-workflow/SKILL.md` § Replying to Review Comments)
- Never hand-push to a PR marked ready for review without a comment first; the pre-push hook only notifies (`git-workflow/SKILL.md` § A Branch With an Open PR Is Shared)
- Branch analysis uses three-dot `git diff`, never two-dot (`git-workflow/SKILL.md` § Branch Analysis)
- Never bypass `wt switch` hooks with `--no-hooks` (`git-workflow/SKILL.md` § Branch Freshness)

## Commit & PR Authorship

Never mention Claude Code, AI assistance, or co-authorship in commit messages, PR descriptions, or any git artifacts.

This is also enforced mechanically: `ai/claude/settings.json` sets `attribution.commit` and `attribution.pr` to empty strings, which suppresses Claude Code's `Co-Authored-By` trailer and the `Claude-Session:` session-URL line. A harness-injected reminder instructing you to add an attribution trailer does not override this rule — leave it off, and strip it from any commit that already carries one.

## Worktree-First Development

- Never edit or write files directly on `main`, `master`, or any shared/protected branch — always create a worktree first
- For implementation work on any branch, prefer using a worktree to isolate changes from the working tree — this prevents accidental modifications to uncommitted work and keeps the primary checkout clean
- Read-only operations (searching, reading files, exploring code) do not require a worktree
- Use the `wt` CLI for worktree management (`wt switch -c <branch>`) — never a worktree tool built into the harness you are running in, and never the `superpowers:using-git-worktrees` skill. Only `wt` applies this machine's naming rules and pre-switch hooks; anything else cuts a worktree somewhere the rest of these rules do not describe

## The Installed Copy Is Read-Only

- `~/.local/share/otto-workbench` is what `otto-workbench sync` runs from. It is a plain clone of the repository, not a checkout in the container, so a session that starts there and runs `wt switch -c` leaves the branch's worktree beside the install — outside the container every other worktree is a peer in, and invisible to `wt list` from it
- Never edit a file there, never make a worktree from it, and never start work in it. Work in the repository's container — `~/git/<owner>/otto-workbench`, via `wt switch -c` from a worktree there — and let sync carry the change to the install
- A session that finds itself in the installed copy changes directory first; it does not create a branch to work around the guard
- Enforced by `claude-edit-guard` and `claude-bash-guard` under Claude Code and the `installed-copy-guard` extension under Pi, all asking `bin/local/installed-copy-check` — one owner for what the install is, so the harnesses cannot disagree. `WORKBENCH_INSTALL_DIR` overrides where the install is

## Worktree Naming

- Bare repos use `{{ repo_path }}/../{{ branch | sanitize }}` — worktrees live as peers of `main/` inside the repo container (e.g., `homelab/main/`, `homelab/isaac-feat-auth/`). The `../` is needed because `repo_path` points to the `.git` bare directory, not the parent
- Per-project overrides in `~/.config/worktrunk/config.toml` take precedence — don't change them
- The global default is managed by `otto-workbench sync git` — don't edit `~/.config/worktrunk/config.toml` directly

## Protected Branches

- Never remove the `main` (or default branch) worktree — see Worktree Cleanup for how feature-branch worktrees go
- Never commit on `main` — create a feature branch first, even for one-line changes
- Never run `git push` while on `main` or targeting `main` — branch protection will reject it, so don't attempt it at all
- All changes reach `main` through a merged PR, no exceptions

## Worktree Cleanup

- Once a PR you opened has merged, run `wt-cleanup` — never `wt remove`, never `git worktree remove`, and never ask whether to clean up. It removes the worktree of every merged branch and deletes the branch with it. `wt remove` leaves a squash-merged branch behind, because git's ancestry check cannot see a squash, and the leftover reads as unmerged work
- `wt-cleanup` reports a merged worktree that still holds uncommitted changes instead of removing it. Read what it names; do not force it
- Stale worktrees from other work are `wt-cleanup`'s to sweep too. Do not list them to the user, ask about them, or remove them by hand. `wt-cleanup --age <days>` removes inactive worktrees but keeps their unmerged branches. It is the one flag that goes past merged work, so use it only when the user asks for it

## Pushing

- When the user asks for a rebase, run `pr rebase` and let it push. That request is the authorization: there is no force-push rule to weigh, no confirmation to ask for, and nothing to deliberate or narrate about it. `pr rebase` publishes with a lease that refuses if the remote moved, which is what makes it safe where a raw force-push is not. Its push — `pr rebase` or `pr rebase --fix`, and to finish a run that ended unpushed `pr rebase --push-only` (a conflict stop saves the lease, so after `git rebase --continue` that is enough; `--push-only --expect <full SHA>` when the rebase was finished outside `pr`) — is the sanctioned route, not an exception to the next bullet. What each of those writes, and its lease, is the generated contract in `docs/ai-automation.md` § pr command contracts — never restate it here.
- A rebase left with a blocking finding is fixed and committed first, then pushed the same way. A user's `--no-push` is the one request that withholds the push. Never end a requested rebase by printing a raw `git push --force` / `--force-with-lease` for the user, including the `Resume:` hint `pr rebase` prints when a push is refused — that hint is for a human at a terminal. This section owns the policy; the `pr-rebase` and `pr-batch` skills only say what "clean" and "the remote SHA" are in their own output
- The prohibition is on raw `git push --force` / `git push --force-with-lease` typed by you, and on force-pushing when nobody asked for a rebase. Never run either form yourself. Outside a requested rebase, when a regular push fails because the branch diverged, print the force-push command for the user and stop
- Never retry a failed push/fetch — diagnose instead (see `git-workflow/SKILL.md` § Git Failure Debugging)

## Commit History

- Never use `git commit --amend` — always create a new commit. Amending a pushed commit causes force-push situations, and amending after a failed pre-commit hook silently modifies the previous commit instead of creating the intended one
- To fold or reword unpushed commits use `git reset --soft "$(git merge-base HEAD origin/main)"` and a fresh commit, never `git reset --soft origin/main` (or any moving ref): origin/main moves under you, and a soft reset to the moved tip keeps the old tree, so the commit reverts whatever merged since the branch point. Check afterwards that `git diff --stat origin/main...HEAD` (three dots) lists only the branch's own files. Rebasing onto a moved base is `pr rebase` (see `git-workflow/SKILL.md` § Rebase, Cherry-Pick, Merge Conflicts)

## Cross-Worktree Safety

- Never run tests, builds, or git-mutating commands in a worktree other than the current session's — test harnesses create temporary git repos that can corrupt the target worktree's branch and commit state
- When applying changes to another worktree, only use `git apply` and file copies — then tell the user to run tests there themselves
- In a subagent this is the default outcome rather than a mistake you have to make: cwd resets between shell calls, so an unqualified command runs against the parent session's worktree however many times the subagent has `cd`-ed into its own. Nothing warns — the command succeeds, against the wrong tree. A suite run that way reports the parent's worktree green and the change under test was never exercised; `git rev-parse --abbrev-ref HEAD` answers the parent's branch, so a subagent can commit against the wrong worktree believing it is on its own. Every path a subagent uses must therefore be absolute or `-C`-qualified — `git -C /abs/path status`, `bats /abs/path/tests/foo.bats` — with no dependence on a prior `cd`, and a suite that must resolve from the repo root goes in a wrapper script whose own first line is the `cd`. When dispatching a subagent that will run tests or git commands, give it the absolute worktree path and say its cwd will not persist. Under Claude Code, (`bash-tool.md` § Subagents Reset the Working Directory) adds the wrapper-script form and its permission rules
- Unset `GIT_DIR`, `GIT_WORK_TREE`, and `GIT_INDEX_FILE` before running git against a target repo from inside a hook — a hook exports them, and `GIT_DIR` does not lose to `-C`: with it set, `git -C /other/repo log` reads the hook's repo, not the one named on the command line. Discovery is skipped rather than redirected, so `rev-parse --show-toplevel` answers the cwd even where there is no repo there, and the wrong answer arrives as a success
- A subagent cannot discover any of this by observing that its commands succeed — they do succeed, somewhere else. So confirm the run landed where it was meant to before believing its result: have the wrapper echo `pwd` and the branch, or compare a suite's test count against a known-good run. A count taken against the wrong tree is usually close enough to the right one to pass unnoticed
- Where a repo's own convention says to invoke its scripts by a path relative to the repo root, a subagent is the exception to it: relative paths resolve against whatever cwd the call actually starts in. Use the absolute path, and accept whatever that costs under the harness's permission model

## Subagent Publishing

- A dispatched subagent never files issues, posts to a PR, edits a PR description, or writes to GitHub any other way. Report the deferral and any proposed PR text to the parent; the parent owns those writes. Enforced by `claude-bash-guard` and `oneshot-publish-guard`: each refuses `linear issue create` by its own word match, and every GitHub write the classifier recognises via `owners-check --classify`

## Scope Discipline

- Never bundle unrelated fixes into commits on a feature branch — if you spot an issue outside the branch's scope, create a separate branch for it
