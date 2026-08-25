#!/usr/bin/env bats
# The git sandbox proves itself: a repo a test creates reads none of the
# machine's git configuration, and a hook the test itself plants still runs.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  REPO="$TMPDIR/repo"
  mkdir -p "$REPO"
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  git -C "$REPO" init -q --initial-branch=main
  git -C "$REPO" config user.email test@example.com
  git -C "$REPO" config user.name Test
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _fake_global CONTENT — a global git config of the test's own, holding CONTENT.
_fake_global() {
  printf '%s\n' "$1" > "$TMPDIR/gitconfig"
  export GIT_CONFIG_GLOBAL="$TMPDIR/gitconfig"
}

# _reject_commits — a repo-local pre-commit hook that refuses every commit.
_reject_commits() {
  printf '#!/usr/bin/env bash\nexit 1\n' > "$REPO/.git/hooks/pre-commit"
  chmod +x "$REPO/.git/hooks/pre-commit"
}

# _commit — stages a file and commits it, reporting the outcome in $status.
_commit() {
  echo one > "$REPO/f.txt"
  git -C "$REPO" add f.txt
  run git -C "$REPO" commit -qm one
}

# ── what a temp repo does not inherit ────────────────────────────────────────
#
# Each of these is on for real on a workbench machine, and each costs a test
# something it never asked for: an orphaned `git fsmonitor--daemon` holding the
# bats runner's stdout, an index rewrite, a gitleaks scan of every staged file.

@test "a test repo inherits no fsmonitor" {
  run git -C "$REPO" config --get core.fsmonitor
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

@test "a test repo inherits no untracked cache" {
  run git -C "$REPO" config --get core.untrackedCache
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

@test "a test repo inherits no global hooks path" {
  run git -C "$REPO" config --get core.hooksPath
  [ "$status" -ne 0 ]
  [ -z "$output" ]
}

# The three cases above would pass just as well on a machine that never set any
# of them, so they report nothing until the sandbox is known to be what git
# reads. Lifting it is the only way to say that.
@test "the sandbox is what hides them" {
  _fake_global '[core]
	fsmonitor = true'

  run git -C "$REPO" config --get core.fsmonitor
  [ "$status" -eq 0 ]
  [ "$output" = "true" ]
}

# ── what it leaves alone ─────────────────────────────────────────────────────

# A `-c core.hooksPath` would outrank the repo and silently turn every test that
# asserts on a hook's refusal into a passing one — push_branch.bats reads a
# post-receive that rewinds the ref, and reports a lost push it never saw.
@test "a hook the test plants in the repo still runs" {
  _reject_commits

  _commit
  [ "$status" -ne 0 ]
}

# sync_git writes core.hooksPath with `git config --global`, and git cannot lock
# /dev/null — so the sandbox is an empty file rather than a null device.
@test "a test can write its own global config" {
  run git config --global user.name sandboxed
  [ "$status" -eq 0 ]

  run git config --global --get user.name
  [ "$output" = "sandboxed" ]
}

@test "a test can point the global config at a file of its own" {
  _fake_global '[user]
	name = testuser'

  run git config --global --get user.name
  [ "$output" = "testuser" ]
}
