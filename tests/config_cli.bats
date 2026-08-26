#!/usr/bin/env bats
# Tests for `otto-workbench config` — the dispatch in bin/otto-workbench and the
# guard the writer behind it exists for: a key the workbench does not read is
# refused instead of written into a file every repo on the machine shares. The
# key surface itself is Python and is covered by tests/test_workbench_config.py.
#
# Every test puts a launcher of its own on PATH, because that symlink is what
# `check_key` resolves "the installed workbench" through. Left alone it would be
# whatever this machine has installed, and these assertions would depend on
# which commit that checkout sits on.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  CONFIG="$WORKBENCH_CONFIG_DIR/config.yml"

  install_launcher "$REPO_ROOT"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# install_launcher CHECKOUT — put CHECKOUT's launcher on PATH, so the config
# writer treats CHECKOUT as the workbench this machine has installed. Calling it
# a second time re-points the symlink and leaves PATH alone, which is what a
# test that replaces setup()'s launcher with a stale one is asking for.
install_launcher() {
  mkdir -p "$TMPDIR/bin"
  ln -sf "$1/bin/otto-workbench" "$TMPDIR/bin/otto-workbench"
  [[ ":$PATH:" == *":$TMPDIR/bin:"* ]] || PATH="$TMPDIR/bin:$PATH"
}

# make_stale_checkout DIR — a checkout whose schema still nests issue_tracker
# under review, which is where the key lived before it was lifted to the top
# level. Only the two files the resolution touches: a launcher to resolve
# through, and the schema beside it.
make_stale_checkout() {
  mkdir -p "$1/bin"
  printf '#!/bin/bash\nexit 0\n' > "$1/bin/otto-workbench"
  chmod +x "$1/bin/otto-workbench"
  python3 -c '
import json, sys
schema = json.load(open(sys.argv[1]))
tracker = schema["properties"].pop("issue_tracker")
schema["properties"]["review"]["properties"]["issue_tracker"] = tracker
json.dump(schema, open(sys.argv[2], "w"))
' "$REPO_ROOT/config.schema.json" "$1/config.schema.json"
}

# ─── Dispatch ────────────────────────────────────────────────────────────────

@test "config --help prints usage and names both sides" {
  run "$REPO_ROOT/bin/otto-workbench" config --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: otto-workbench config"* ]]
  [[ "$output" == *"set KEY VALUE"* ]]
  [[ "$output" == *"status"* ]]
  [[ "$output" == *"get KEY"* ]]
}

@test "config with no subcommand prints usage and fails" {
  run "$REPO_ROOT/bin/otto-workbench" config
  [ "$status" -eq 1 ]
  [[ "$output" == *"Usage: otto-workbench config"* ]]
}

@test "an unknown config subcommand prints usage and fails" {
  run "$REPO_ROOT/bin/otto-workbench" config nonsense
  [ "$status" -eq 1 ]
  [[ "$output" == *"Usage: otto-workbench config"* ]]
}

@test "config is listed in the top-level usage" {
  run "$REPO_ROOT/bin/otto-workbench" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"config"* ]]
}

@test "set without a value is a usage error, not a write" {
  run "$REPO_ROOT/bin/otto-workbench" config set reuse.level
  [ "$status" -eq 2 ]
  [ ! -f "$CONFIG" ]
}

# ─── The write ───────────────────────────────────────────────────────────────

@test "set writes the key and says where it landed" {
  run "$REPO_ROOT/bin/otto-workbench" config set reuse.level ultra
  [ "$status" -eq 0 ]
  [[ "$output" == *"reuse.level = ultra"* ]]
  [[ "$output" == *"$CONFIG"* ]]

  run grep -c "ultra" "$CONFIG"
  [ "$status" -eq 0 ]
}

@test "set --project writes the repo's file and leaves the global one alone" {
  git init --quiet "$TMPDIR/repo"
  cd "$TMPDIR/repo" || return 1
  _assert_not_real_repo || return 1

  run "$REPO_ROOT/bin/otto-workbench" config set issue_tracker.provider github --project
  [ "$status" -eq 0 ]
  [[ "$output" == *".workbench.yml"* ]]

  run grep -c "github" "$TMPDIR/repo/.workbench.yml"
  [ "$status" -eq 0 ]
  [ ! -f "$CONFIG" ]
}

@test "set --project outside a repo refuses instead of guessing a root" {
  cd "$TMPDIR" || return 1
  run "$REPO_ROOT/bin/otto-workbench" config set reuse.level ultra --project
  [ "$status" -eq 1 ]
  [[ "$output" == *"needs a git repo"* ]]
}

# _make_container — the bare-repo layout under TMPDIR, and the shell standing in
# its `main` worktree.
_make_container() {
  mkdir -p "$TMPDIR/seed"
  printf 'x\n' > "$TMPDIR/seed/a.sh"
  make_container_seed "$TMPDIR/seed"
  make_worktree_container "$TMPDIR/container" "$TMPDIR/seed"
  cd "$TMPDIR/container/main" || return 1
  _assert_not_real_repo || return 1
}

@test "set --container writes above the worktrees, not into the checkout" {
  _make_container

  run "$REPO_ROOT/bin/otto-workbench" config set issue_tracker.provider github --container
  [ "$status" -eq 0 ]
  [[ "$output" == *"$TMPDIR/container/.workbench.yml"* ]]
  [[ "$output" == *"every worktree"* ]]

  run grep -c "github" "$TMPDIR/container/.workbench.yml"
  [ "$status" -eq 0 ]
  [ ! -f "$TMPDIR/container/main/.workbench.yml" ]
  [ ! -f "$CONFIG" ]
}

@test "set --container in a plain clone refuses instead of writing the worktree" {
  _make_repo

  run "$REPO_ROOT/bin/otto-workbench" config set issue_tracker.provider github --container
  [ "$status" -eq 1 ]
  [[ "$output" == *"no container"* ]]
  [ ! -f "$TMPDIR/repo/.workbench.yml" ]
}

@test "set --container outside a repo refuses instead of guessing a root" {
  cd "$TMPDIR" || return 1
  run "$REPO_ROOT/bin/otto-workbench" config set reuse.level ultra --container
  [ "$status" -eq 1 ]
  [[ "$output" == *"--container needs a git repo"* ]]
}

@test "--project and --container are mutually exclusive" {
  _make_container

  run "$REPO_ROOT/bin/otto-workbench" config set reuse.level ultra --project --container
  [ "$status" -eq 2 ]
  [ ! -f "$TMPDIR/container/.workbench.yml" ]
  [ ! -f "$TMPDIR/container/main/.workbench.yml" ]
}

# ─── The report ──────────────────────────────────────────────────────────────
#
# What each scope resolved to is Python and is covered by
# tests/test_workbench_config.py. These are the dispatch, the exit codes, and
# that the rendering actually puts a source next to a value.

# _make_repo — a git repo under TMPDIR, and the shell standing in it.
_make_repo() {
  git init --quiet "$TMPDIR/repo"
  cd "$TMPDIR/repo" || return 1
  _assert_not_real_repo || return 1
}

@test "status lists every scope, project first, with its path" {
  _make_repo

  run "$REPO_ROOT/bin/otto-workbench" config status
  [ "$status" -eq 0 ]
  [[ "$output" == *"project"*"$TMPDIR/repo/.workbench.yml"* ]]
  [[ "$output" == *"global"*"$CONFIG"* ]]
  # Precedence order, not alphabetical or merge order.
  [[ "${output#*project}" == *global* ]]
}

@test "status lists the container between the two older scopes" {
  _make_container

  run "$REPO_ROOT/bin/otto-workbench" config status
  [ "$status" -eq 0 ]
  [[ "$output" == *"container"*"$TMPDIR/container/.workbench.yml"* ]]
  [[ "${output#*project}" == *container* ]]
  [[ "${output#*container}" == *global* ]]
}

@test "status names the container as the source of a value it set" {
  _make_container
  run "$REPO_ROOT/bin/otto-workbench" config set issue_tracker.provider github --container
  [ "$status" -eq 0 ]

  run "$REPO_ROOT/bin/otto-workbench" config status
  [ "$status" -eq 0 ]
  [[ "$output" == *"issue_tracker.provider"*"github"*"container"* ]]
}

@test "status marks a scope with no file" {
  _make_repo

  run "$REPO_ROOT/bin/otto-workbench" config status
  [ "$status" -eq 0 ]
  [[ "$output" == *"(no file)"* ]]
}

@test "status outside a repo reports the global scope alone" {
  cd "$TMPDIR" || return 1

  run "$REPO_ROOT/bin/otto-workbench" config status
  [ "$status" -eq 0 ]
  [[ "$output" == *"global"* ]]
  [[ "$output" != *".workbench.yml"* ]]
}

@test "status names the file a value came from, and calls the rest defaults" {
  _make_repo
  run "$REPO_ROOT/bin/otto-workbench" config set reuse.level ultra
  [ "$status" -eq 0 ]

  run "$REPO_ROOT/bin/otto-workbench" config status
  [ "$status" -eq 0 ]
  [[ "$output" == *"reuse.level"*"ultra"*"global"* ]]
  [[ "$output" == *"reuse.default"*"full"*"default"* ]]
}

@test "status reports a key nothing reads, and still exits 0" {
  _make_repo
  mkdir -p "$WORKBENCH_CONFIG_DIR"
  printf 'review:\n  issue_tracker:\n    provider: github\n' > "$CONFIG"

  run "$REPO_ROOT/bin/otto-workbench" config status
  [ "$status" -eq 0 ]
  [[ "$output" == *"Keys nothing reads"* ]]
  [[ "$output" == *"review.issue_tracker.provider"* ]]
}

@test "status fails and names the file when a scope cannot be read" {
  _make_repo
  printf 'reuse: [unclosed\n' > "$TMPDIR/repo/.workbench.yml"

  run "$REPO_ROOT/bin/otto-workbench" config status
  [ "$status" -eq 1 ]
  [[ "$output" == *"$TMPDIR/repo/.workbench.yml"* ]]
}

@test "status takes no arguments" {
  run "$REPO_ROOT/bin/otto-workbench" config status extra
  [ "$status" -eq 2 ]
}

# ─── The read ────────────────────────────────────────────────────────────────
#
# Which scope answers for a key is Python and is covered by
# tests/config_cli_get_test.py. These are the dispatch and the exit codes — that
# a bash caller reaching this through the launcher gets a record it can split.

@test "get prints one record for the repo the caller is standing in" {
  _make_repo
  printf 'issue_tracker:\n  provider: github\n' > "$TMPDIR/repo/.workbench.yml"

  run "$REPO_ROOT/bin/otto-workbench" config get issue_tracker.provider
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf 'project\tgithub\t%s' "$TMPDIR/repo")" ]
}

@test "get prints one record per named repo, in the order given" {
  _make_repo
  git init --quiet "$TMPDIR/other"
  printf 'issue_tracker:\n  provider: linear\n' > "$TMPDIR/other/.workbench.yml"

  run "$REPO_ROOT/bin/otto-workbench" config get issue_tracker.provider \
    "$TMPDIR/other" "$TMPDIR/repo"
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "$(printf 'project\tlinear\t%s' "$TMPDIR/other")" ]
  [ "${lines[1]}" = "$(printf 'default\t\t%s' "$TMPDIR/repo")" ]
}

@test "get needs a key" {
  run "$REPO_ROOT/bin/otto-workbench" config get
  [ "$status" -eq 2 ]
}

@test "get refuses a key nothing reads" {
  _make_repo
  run "$REPO_ROOT/bin/otto-workbench" config get reuse.levl
  [ "$status" -eq 1 ]
  [[ "$output" == *"reuse.levl"* ]]
}

@test "get is not blocked by a stale installed workbench" {
  # The write guard's counterpart. A write outlives the checkout that made it,
  # so it is judged by the installed schema too; a read resolves here and now,
  # and a branch whose own tests could not read its own new key could not test
  # it at all.
  _make_repo
  printf 'issue_tracker:\n  provider: github\n' > "$TMPDIR/repo/.workbench.yml"
  make_stale_checkout "$TMPDIR/installed"
  install_launcher "$TMPDIR/installed"

  run "$REPO_ROOT/bin/otto-workbench" config get issue_tracker.provider
  [ "$status" -eq 0 ]
  [[ "$output" == *"github"* ]]
}

# ─── The guard ───────────────────────────────────────────────────────────────

@test "a key the workbench does not define is refused, and nothing is written" {
  run "$REPO_ROOT/bin/otto-workbench" config set reuse.levl ultra
  [ "$status" -eq 1 ]
  [[ "$output" == *"reuse.levl"* ]]
  [[ "$output" == *"config.schema.json"* ]]
  [ ! -f "$CONFIG" ]
}

@test "a key the installed workbench does not read is refused" {
  make_stale_checkout "$TMPDIR/installed"
  install_launcher "$TMPDIR/installed"

  run "$REPO_ROOT/bin/otto-workbench" config set issue_tracker.provider github
  [ "$status" -eq 1 ]
  [[ "$output" == *"issue_tracker.provider"* ]]
  [[ "$output" == *"$TMPDIR/installed/config.schema.json"* ]]
  [ ! -f "$CONFIG" ]
}

@test "--project is refused by the same check, and writes no file either" {
  git init --quiet "$TMPDIR/repo"
  cd "$TMPDIR/repo" || return 1
  _assert_not_real_repo || return 1

  run "$REPO_ROOT/bin/otto-workbench" config set issue_tracker.provdier github --project
  [ "$status" -eq 1 ]
  [[ "$output" == *"issue_tracker.provdier"* ]]
  [ ! -f "$TMPDIR/repo/.workbench.yml" ]
}

@test "the key the incident wrote is refused by this checkout on its own" {
  run "$REPO_ROOT/bin/otto-workbench" config set review.issue_tracker.provider github
  [ "$status" -eq 1 ]
  [[ "$output" == *"review.issue_tracker.provider"* ]]
  [ ! -f "$CONFIG" ]
}

@test "a key both checkouts read is written even when they differ elsewhere" {
  make_stale_checkout "$TMPDIR/installed"
  install_launcher "$TMPDIR/installed"

  run "$REPO_ROOT/bin/otto-workbench" config set reuse.level ultra
  [ "$status" -eq 0 ]
  run grep -c "ultra" "$CONFIG"
  [ "$status" -eq 0 ]
}
