#!/usr/bin/env bats
# Tests for step_claude_rules — the half of the rules pipeline that is Claude
# Code's: which of the merged rules it is scoped to load, and the symlink layout
# it wants them in.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"
  export NO_COLOR=1

  FAKE_WORKBENCH="$TMPDIR/workbench"
  RULES="$FAKE_WORKBENCH/ai/guidelines/rules"
  OVERRIDE_RULES="$WORKBENCH_CONFIG_DIR/overrides/ai/guidelines/rules"
  GENERATED_RULES="$WORKBENCH_STATE_DIR/rules"
  INSTALLED="$HOME/.claude/rules"
  mkdir -p "$HOME" "$RULES" "$OVERRIDE_RULES" "$GENERATED_RULES"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _write_rule DIR NAME FRONTMATTER BODY — writes one rule into a layer.
_write_rule() {
  local dir="$1" name="$2" fm="$3" body="$4"
  if [[ -n "$fm" ]]; then
    printf -- '---\n%s\n---\n%s\n' "$fm" "$body" > "$dir/$name"
  else
    printf -- '%s\n' "$body" > "$dir/$name"
  fi
}

_rule()           { _write_rule "$RULES" "$@"; }
_local_rule()     { _write_rule "$OVERRIDE_RULES" "$@"; }
_generated_rule() { _write_rule "$GENERATED_RULES" "$@"; }

_run_step() {
  run bash -c '
    HOME="$2"
    WORKBENCH_DIR="$3"
    . "$1/lib/ui.sh"
    . "$1/ai/claude/steps.sh"
    step_claude_rules
  ' _ "$REPO_ROOT" "$HOME" "$FAKE_WORKBENCH"
}

@test "every layer is installed as a symlink to its source" {
  _rule general.md "" "REPO BODY"
  _local_rule testing.local.md "" "LOCAL BODY"
  _generated_rule workbench.md "" "GENERATED BODY"

  _run_step
  [ "$status" -eq 0 ]
  [ "$(readlink "$INSTALLED/general.md")" = "$RULES/general.md" ]
  [ "$(readlink "$INSTALLED/testing.local.md")" = "$OVERRIDE_RULES/testing.local.md" ]
  [ "$(readlink "$INSTALLED/workbench.md")" = "$GENERATED_RULES/workbench.md" ]
}

@test "an override replaces the repo rule it shares a name with" {
  _rule general.md "" "REPO BODY"
  _local_rule general.md "" "OVERRIDE BODY"

  _run_step
  [ "$(readlink "$INSTALLED/general.md")" = "$OVERRIDE_RULES/general.md" ]
}

@test "a rule scoped away from claude is not installed" {
  _write_rule "$RULES" pi-only.md 'harness: [pi]' "PI ONLY"
  _rule shared.md "" "SHARED"

  _run_step
  [ ! -e "$INSTALLED/pi-only.md" ]
  [ -L "$INSTALLED/shared.md" ]
}

@test "a rule naming claude among its harnesses is installed" {
  _write_rule "$RULES" both.md 'harness: [claude, pi]' "BOTH"

  _run_step
  [ -L "$INSTALLED/both.md" ]
}

@test "a rule that stops naming claude is pruned on the next sync" {
  _rule shared.md "" "SHARED"
  _run_step
  [ -L "$INSTALLED/shared.md" ]

  _write_rule "$RULES" shared.md 'harness: [pi]' "PI ONLY NOW"
  _run_step
  [ ! -e "$INSTALLED/shared.md" ]
}

@test "a retired rule is pruned on the next sync" {
  _rule general.md "" "GENERAL"
  _rule doomed.md "" "DOOMED"
  _run_step
  [ -L "$INSTALLED/doomed.md" ]

  rm "$RULES/doomed.md"
  _run_step
  [ -L "$INSTALLED/general.md" ]
  [ ! -e "$INSTALLED/doomed.md" ]
}

@test "a link the operator made by hand is left alone" {
  # Pruning keys on the link's target sitting under one of the layer roots, so
  # a rule an operator symlinked in from somewhere else is not ours to remove.
  _rule general.md "" "GENERAL"
  mkdir -p "$INSTALLED"
  echo "MINE" > "$TMPDIR/mine.md"
  ln -s "$TMPDIR/mine.md" "$INSTALLED/mine.md"

  _run_step
  [ -L "$INSTALLED/mine.md" ]
}

@test "an old real-file copy is replaced by a symlink" {
  _rule general.md "" "REPO BODY"
  mkdir -p "$INSTALLED"
  echo "STALE COPY" > "$INSTALLED/general.md"

  _run_step
  [ -L "$INSTALLED/general.md" ]
  grep -q "REPO BODY" "$INSTALLED/general.md"
}
