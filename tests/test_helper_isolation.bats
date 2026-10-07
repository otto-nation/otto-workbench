#!/usr/bin/env bats
# The git sandbox proves itself: a repo a test creates reads none of the
# machine's git configuration, and a hook the test itself plants still runs.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  REPO="$TMPDIR/repo"
  mkdir -p "$REPO"
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  git -C "$REPO" init -q --initial-branch=main
  git -C "$REPO" config user.email test@example.com
  git -C "$REPO" config user.name Test
}

teardown() {
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
# asserts on a hook's refusal into a passing one — a suite reading a
# post-receive that rewinds the ref would report a lost push it never saw.
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

# ── live agent CLIs ──────────────────────────────────────────────────────────

@test "common_setup shadows a live claude and pi CLI" {
  # Outside BATS_TEST_TMPDIR: a binary under it reads as the test's own stub.
  local realbin="$BATS_FILE_TMPDIR/shadows-a-live-cli/realbin"
  mkdir -p "$realbin"
  local name
  for name in claude pi; do
    printf '#!/usr/bin/env bash\necho reached-real-%s\nexit 0\n' "$name" \
      > "$realbin/$name"
    chmod +x "$realbin/$name"
  done
  PATH="$realbin:$PATH"
  common_setup

  run --separate-stderr claude
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"reached the real claude CLI"* ]]

  run --separate-stderr pi
  [ "$status" -eq 1 ]
  [[ "$stderr" == *"reached the real pi CLI"* ]]
}

@test "a later stub still wins over the live-backend guard" {
  local realbin="$BATS_FILE_TMPDIR/later-stub-wins/realbin"
  mkdir -p "$realbin" "$TMPDIR/mystub"
  printf '#!/usr/bin/env bash\necho reached-real\n' > "$realbin/claude"
  chmod +x "$realbin/claude"
  PATH="$realbin:$PATH"
  common_setup
  printf '#!/usr/bin/env bash\necho my-stub\n' > "$TMPDIR/mystub/claude"
  chmod +x "$TMPDIR/mystub/claude"
  PATH="$TMPDIR/mystub:$PATH"

  run claude
  [ "$status" -eq 0 ]
  [ "$output" = "my-stub" ]
}

@test "common_setup leaves a stub the test already put on PATH in front" {
  mkdir -p "$TMPDIR/mystub"
  printf '#!/usr/bin/env bash\necho my-stub\n' > "$TMPDIR/mystub/claude"
  chmod +x "$TMPDIR/mystub/claude"
  PATH="$TMPDIR/mystub:$PATH"
  # A helper (make_git_remote and friends) calling common_setup mid-test.
  common_setup

  run claude
  [ "$status" -eq 0 ]
  [ "$output" = "my-stub" ]
}

# ── narrow_path_to and version-manager shims ─────────────────────────────────
#
# A shim decides what to run at call time, from config and installs a narrowed
# PATH and a swapped HOME take away. Linked into narrow-bin it fails on every
# call, so narrow_path_to links what the shim stands for instead.

# _shim_fixture — a failing yq in a shims/ dir, a working yq behind it, and a
# mise that prints $MISE_WHICH_ANSWER (or fails when it is empty).
_shim_fixture() {
  mkdir -p "$TMPDIR/vm/shims" "$TMPDIR/real" "$TMPDIR/installed" "$TMPDIR/mise-bin"
  printf '#!/bin/sh\necho "shim: no version is set" >&2\nexit 1\n' > "$TMPDIR/vm/shims/yq"
  printf '#!/bin/sh\necho real-yq\n' > "$TMPDIR/real/yq"
  printf '#!/bin/sh\necho installed-yq\n' > "$TMPDIR/installed/yq"
  printf '#!/bin/sh\n[ -n "$MISE_WHICH_ANSWER" ] || exit 1\necho "$MISE_WHICH_ANSWER"\n' \
    > "$TMPDIR/mise-bin/mise"
  chmod +x "$TMPDIR/vm/shims/yq" "$TMPDIR/real/yq" "$TMPDIR/installed/yq" "$TMPDIR/mise-bin/mise"
  PATH="$TMPDIR/mise-bin:$TMPDIR/vm/shims:$TMPDIR/real:/usr/bin:/bin"
}

@test "narrow_path_to links what mise says a shim runs, not the shim" {
  _shim_fixture
  export MISE_WHICH_ANSWER="$TMPDIR/installed/yq"
  narrow_path_to yq
  run yq
  [ "$status" -eq 0 ]
  [ "$output" = installed-yq ]
}

@test "narrow_path_to falls past a shim mise cannot resolve to the next yq on PATH" {
  _shim_fixture
  export MISE_WHICH_ANSWER=""
  narrow_path_to yq
  run yq
  [ "$status" -eq 0 ]
  [ "$output" = real-yq ]
}

@test "narrow_path_to leaves out a tool that is only a shim mise cannot resolve" {
  _shim_fixture
  rm "$TMPDIR/real/yq"
  export MISE_WHICH_ANSWER=""
  run --separate-stderr narrow_path_to yq
  [ "$status" -eq 0 ]
  [[ "$stderr" == *"no runnable yq on PATH"* ]]
  [ ! -e "$BATS_TEST_TMPDIR/narrow-bin/yq" ]
}
