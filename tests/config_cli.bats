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

@test "config --help prints usage and names the write" {
  run "$REPO_ROOT/bin/otto-workbench" config --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage: otto-workbench config"* ]]
  [[ "$output" == *"set KEY VALUE"* ]]
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
