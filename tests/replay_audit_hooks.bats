#!/usr/bin/env bats
# Tests for the global prepare-commit-msg and post-rewrite hooks: the replay
# audit they run, and the repo-local hooks they delegate to.
#
# Exercised through real rebases and cherry-picks rather than by calling the
# hooks by hand. What is under test is mostly git's arrangement — that
# `rebase --continue` runs prepare-commit-msg although it commits with `-n`,
# that a non-zero exit leaves the rebase stopped on the same commit, what
# post-rewrite's stdin holds after a `--skip` — and a hand-run hook would
# assert the harness instead.
#
# GIT_CONFIG_GLOBAL points at a temp gitconfig whose core.hooksPath holds
# symlinks to this checkout's hooks, as step_global_hooks installs them.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup

  mkdir -p "$TMPDIR/hooks"
  ln -sf "$REPO_ROOT/git/hooks/prepare-commit-msg" "$TMPDIR/hooks/prepare-commit-msg"
  ln -sf "$REPO_ROOT/git/hooks/post-rewrite" "$TMPDIR/hooks/post-rewrite"

  export GIT_CONFIG_GLOBAL="$TMPDIR/gitconfig"
  export GIT_CONFIG_SYSTEM=/dev/null
  # `rebase --continue` opens an editor for the message after a conflict, and
  # with no tty `vi` waits on stdin for ever. Pinned in the environment because
  # GIT_EDITOR outranks every config a test might otherwise rely on.
  export GIT_EDITOR=true
  unset "${!WORKBENCH_ALLOW_@}"
  git config --global core.hooksPath "$TMPDIR/hooks"
  git config --global user.name t
  git config --global user.email t@t
  git config --global init.defaultBranch main
  git config --global commit.gpgsign false

  # feat's one commit changes line 2, which collides with main, and line 6,
  # which does not — the clean change a whole-file `--ours` throws away. It
  # also adds h, so taking f whole still leaves a commit to make: a commit
  # emptied by the resolution is dropped without one, which is post-rewrite's
  # case rather than prepare-commit-msg's.
  W="$TMPDIR/wt"
  git init -q "$W"
  printf 'a\nb\nc\nd\ne\nf\ng\n' > "$W/f"
  git -C "$W" add f
  git -C "$W" commit -q -m base
  git -C "$W" checkout -q -b feat
  printf 'a\nB\nc\nd\ne\nF\ng\n' > "$W/f"
  echo h > "$W/h"
  git -C "$W" add f h
  git -C "$W" commit -q -m "feat: edit"
  git -C "$W" checkout -q main
  printf 'a\nBM\nc\nD\ne\nf\ng\n' > "$W/f"
  git -C "$W" commit -q -am "main: edit"
  git -C "$W" checkout -q feat
}

teardown() {
  common_teardown
}

# stop_on_conflict — rebase feat onto main, which halts on f.
stop_on_conflict() {
  run git -C "$W" rebase main
  [ "$status" -ne 0 ]
  [ "$(git -C "$W" diff --name-only --diff-filter=U)" = f ]
}

# take_ours — the whole-file resolution this suite exists to catch.
take_ours() {
  git -C "$W" checkout --ours -- f
  git -C "$W" add f
}

# `--git-path` answers relative to the repo, not to this shell's cwd.
rebase_in_progress() {
  [ -d "$(git -C "$W" rev-parse --absolute-git-dir)/rebase-merge" ]
}

# Same reasoning as `rebase_in_progress`, for a stopped cherry-pick.
cherry_pick_in_progress() {
  [ -f "$(git -C "$W" rev-parse --absolute-git-dir)/CHERRY_PICK_HEAD" ]
}

# ── prepare-commit-msg ───────────────────────────────────────────────────────

@test "a whole-file --ours resolution is refused and the rebase stays stopped" {
  stop_on_conflict
  take_ours

  run git -C "$W" rebase --continue
  [ "$status" -ne 0 ]
  [[ "$output" == *"refusing to commit"* ]]
  [[ "$output" == *"git checkout -m -- f"* ]]
  rebase_in_progress
  # The resolution is still staged, ready to be redone or overridden.
  # `$(...)` strips trailing newlines on both sides, so the no-trailing-newline
  # `printf` here matches the `git show` blob despite it ending in `\n`.
  [ "$(git -C "$W" show :f)" = "$(printf 'a\nBM\nc\nD\ne\nf\ng')" ]
}

@test "the printed recovery restores the conflict, and a real merge then continues" {
  stop_on_conflict
  take_ours
  run git -C "$W" rebase --continue
  [ "$status" -ne 0 ]

  git -C "$W" checkout -m -- f
  printf 'a\nBM+B\nc\nD\ne\nF\ng\n' > "$W/f"
  git -C "$W" add f
  run git -C "$W" rebase --continue
  [ "$status" -eq 0 ]
  [[ "$output" != *"refusing"* ]]
  run ! rebase_in_progress
  [ "$(git -C "$W" log -1 --format=%s)" = "feat: edit" ]
}

@test "the override commits a refused resolution, and says it did" {
  stop_on_conflict
  take_ours

  # A prefix assignment on `run`, a shell function, reaches the git it runs.
  WORKBENCH_ALLOW_DROPPED_CHANGES=1 run git -C "$W" rebase --continue
  [ "$status" -eq 0 ]
  [[ "$output" == *"committing anyway"* ]]
  run ! rebase_in_progress
}

@test "a one-sided collision with every clean change kept is advisory only" {
  stop_on_conflict
  printf 'a\nBM\nc\nD\ne\nF\ng\n' > "$W/f"
  git -C "$W" add f

  run git -C "$W" rebase --continue
  [ "$status" -eq 0 ]
  [[ "$output" == *"Resolved to one side only"* ]]
  run ! rebase_in_progress
}

@test "a whole-file resolution replayed by rerere is refused like a typed one" {
  # rerere records whatever a conflict was resolved to — a whole-file take
  # included — and replays it unasked the next time the conflict recurs.
  git -C "$W" config rerere.enabled true
  stop_on_conflict
  take_ours
  WORKBENCH_ALLOW_DROPPED_CHANGES=1 git -C "$W" rebase --continue 2>/dev/null
  git -C "$W" reset -q --hard ORIG_HEAD

  run git -C "$W" rebase main
  [ "$status" -ne 0 ]
  # This exact wording is git's own rerere porcelain message, not this repo's —
  # if a future git release rewords it, this assertion fails with nothing but
  # a string mismatch to go on.
  [[ "$output" == *"Resolved 'f' using previous resolution"* ]]
  git -C "$W" add f

  run git -C "$W" rebase --continue
  [ "$status" -ne 0 ]
  [[ "$output" == *"refusing to commit"* ]]
}

@test "a cherry-pick refusal names the cherry-pick's own continue" {
  git -C "$W" checkout -q main
  run git -C "$W" cherry-pick feat
  [ "$status" -ne 0 ]
  take_ours

  run git -C "$W" cherry-pick --continue
  [ "$status" -ne 0 ]
  [[ "$output" == *"WORKBENCH_ALLOW_DROPPED_CHANGES=1 git cherry-pick --continue"* ]]
  cherry_pick_in_progress
}

@test "an ordinary commit after a conflicted rebase is not audited against it" {
  # git keeps REBASE_HEAD after a rebase whose last step stopped for a conflict.
  stop_on_conflict
  printf 'a\nBM+B\nc\nD\ne\nF\ng\n' > "$W/f"
  git -C "$W" add f
  git -C "$W" rebase --continue
  git -C "$W" rev-parse -q --verify REBASE_HEAD

  # Undoes the very change the stale commit made — refused, were it audited.
  printf 'a\nBM+B\nc\nD\ne\nf\ng\n' > "$W/f"
  run git -C "$W" commit -qam "an ordinary change"
  [ "$status" -eq 0 ]
  [[ "$output" != *"refusing"* ]]
}

@test "splitting a commit at an edit stop is the user's restructuring, not a loss" {
  GIT_SEQUENCE_EDITOR="sed -i.bak s/^pick/edit/" git -C "$W" rebase -i -q HEAD~1
  git -C "$W" reset -q HEAD^
  printf 'a\nB\nc\nd\ne\nf\ng\n' > "$W/f"

  run git -C "$W" commit -qam "first half"
  [ "$status" -eq 0 ]
  [[ "$output" != *"refusing"* ]]

  printf 'a\nB\nc\nd\ne\nF\ng\n' > "$W/f"
  git -C "$W" add f h
  git -C "$W" commit -q -m "second half"
  run git -C "$W" rebase --continue
  [ "$status" -eq 0 ]
  [ "$(git -C "$W" log --format=%s -2 | tr '\n' ,)" = "second half,first half," ]
}

@test "a failing audit is reported and waved through, never mistaken for a refusal" {
  mkdir -p "$TMPDIR/bin"
  printf '#!/bin/sh\nexit 1\n' > "$TMPDIR/bin/python3"
  chmod +x "$TMPDIR/bin/python3"
  stop_on_conflict
  take_ours

  PATH="$TMPDIR/bin:$PATH" run git -C "$W" rebase --continue
  [ "$status" -eq 0 ]
  [[ "$output" == *"replay audit could not run (exit 1)"* ]]
}

@test "prepare-commit-msg still runs the repo-local hook, with git's arguments" {
  printf '#!/bin/sh\necho "local:$#:$2" > "%s/local-ran"\n' "$TMPDIR" \
    > "$W/.git/hooks/prepare-commit-msg"
  chmod +x "$W/.git/hooks/prepare-commit-msg"

  git -C "$W" commit -q --allow-empty -m plain
  [ "$(cat "$TMPDIR/local-ran")" = "local:2:message" ]
}

# ── post-rewrite ─────────────────────────────────────────────────────────────

@test "a rebase --skip that drops a commit's changes is reported with the way back" {
  stop_on_conflict

  run git -C "$W" rebase --skip
  [ "$status" -eq 0 ]
  [[ "$output" == *"dropped 1 commit(s) whose changes are not in the result"* ]]
  [[ "$output" == *"git cherry-pick $(git -C "$W" rev-parse --short ORIG_HEAD)"* ]]
  [[ "$output" == *"the tip before the rebase"* ]]
}

@test "a resolution that empties the commit is dropped by git, and reported" {
  # A commit touching f alone: taking f whole leaves nothing to commit, so git
  # drops it inside `--continue` and prepare-commit-msg never runs.
  # post-rewrite's stdin is the only record that it happened.
  git -C "$W" checkout -q -b solo main~1
  printf 'a\nB\nc\nd\ne\nF\ng\n' > "$W/f"
  git -C "$W" commit -q -am "solo: edit"
  stop_on_conflict
  take_ours

  run git -C "$W" rebase --continue
  [ "$status" -eq 0 ]
  [[ "$output" == *"dropped 1 commit(s) whose changes are not in the result"* ]]
  [[ "$output" == *"solo: edit"* ]]
  [ "$(git -C "$W" rev-parse HEAD)" = "$(git -C "$W" rev-parse main)" ]
}

@test "a commit dropped because main already has it is not reported" {
  git -C "$W" checkout -q main
  printf 'a\nB\nc\nD\ne\nF\ng\n' > "$W/f"
  echo h > "$W/h"
  git -C "$W" add f h
  git -C "$W" commit -q -m "main: take feat's change too"
  git -C "$W" checkout -q feat

  run git -C "$W" rebase main
  [ "$status" -eq 0 ]
  [[ "$output" != *"dropped"* ]]
  [ "$(git -C "$W" rev-parse HEAD)" = "$(git -C "$W" rev-parse main)" ]
}

@test "one rebase is both recorded and audited by the one post-rewrite hook" {
  # Two jobs share the hook because git hands the map over once: the commit the
  # rebase kept must reach the rewrite record, and the one it dropped must
  # still be reported.
  echo k > "$W/k"
  git -C "$W" add k
  git -C "$W" commit -q -m "feat: keep"
  local kept
  kept="$(git -C "$W" rev-parse HEAD)"
  stop_on_conflict

  run git -C "$W" rebase --skip
  [ "$status" -eq 0 ]
  [[ "$output" == *"dropped 1 commit(s) whose changes are not in the result"* ]]
  [ "$(cat "$W/.git/workbench-rewrites")" = "$kept $(git -C "$W" rev-parse HEAD)" ]
}

@test "post-rewrite hands the repo-local hook the same stdin and arguments" {
  printf '#!/bin/sh\n{ echo "args:$*"; cat; } >> "%s/local-ran"\n' "$TMPDIR" \
    > "$W/.git/hooks/post-rewrite"
  chmod +x "$W/.git/hooks/post-rewrite"
  local old
  old="$(git -C "$W" rev-parse HEAD)"

  git -C "$W" commit -q --amend -m "reworded"
  [ "$(head -1 "$TMPDIR/local-ran")" = "args:amend" ]
  [ "$(sed -n 2p "$TMPDIR/local-ran")" = "$old $(git -C "$W" rev-parse HEAD)" ]
  # Once: an earlier version ran it a second time with no input.
  [ "$(wc -l < "$TMPDIR/local-ran" | tr -d ' ')" -eq 2 ]
}
