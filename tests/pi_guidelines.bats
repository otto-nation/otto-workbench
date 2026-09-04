#!/usr/bin/env bats
# Tests for step_pi_guidelines — the generator that turns the machine's merged
# rule layers into the single context file Pi loads.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_STATE_DIR="$TMPDIR/state"

  # One directory per rule layer, so a test can say which layer a rule is in.
  FAKE_WORKBENCH="$TMPDIR/workbench"
  RULES="$FAKE_WORKBENCH/ai/guidelines/rules"
  OVERRIDE_RULES="$WORKBENCH_CONFIG_DIR/overrides/ai/guidelines/rules"
  GENERATED_RULES="$WORKBENCH_STATE_DIR/rules"
  mkdir -p "$HOME" "$RULES" "$OVERRIDE_RULES" "$GENERATED_RULES" "$FAKE_WORKBENCH/ai/pi"
  cp "$REPO_ROOT/ai/pi/AGENTS.head.md" "$FAKE_WORKBENCH/ai/pi/AGENTS.head.md"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _write_rule DIR NAME FRONTMATTER BODY — writes one rule into a layer.
# FRONTMATTER is the block's inner lines, or empty for a file with none.
_write_rule() {
  local dir="$1" name="$2" fm="$3" body="$4"
  if [[ -n "$fm" ]]; then
    printf -- '---\n%s\n---\n%s\n' "$fm" "$body" > "$dir/$name"
  else
    printf -- '%s\n' "$body" > "$dir/$name"
  fi
}

# _rule / _local_rule / _generated_rule — one per layer, in resolution order.
_rule()           { _write_rule "$RULES" "$@"; }
_local_rule()     { _write_rule "$OVERRIDE_RULES" "$@"; }
_generated_rule() { _write_rule "$GENERATED_RULES" "$@"; }

_run_step() {
  run bash -c '
    HOME="$2"
    WORKBENCH_DIR="$3"
    . "$1/lib/ui.sh"
    . "$1/ai/pi/steps.sh"
    step_pi_guidelines
  ' _ "$REPO_ROOT" "$HOME" "$FAKE_WORKBENCH"
}

@test "an always-on rule reaches the context file" {
  _rule general.md "" "GENERAL RULE BODY"

  _run_step
  [ "$status" -eq 0 ]
  grep -q "GENERAL RULE BODY" "$HOME/.pi/agent/AGENTS.md"
}

@test "a path-scoped rule is left out" {
  _rule general.md "" "GENERAL RULE BODY"
  # Block-sequence form deliberately — it is what every path-scoped rule in
  # ai/guidelines/rules/ is written as, so an inline fixture here would let a
  # same-line-only reader pass the suite and drop nothing in production.
  _rule go.md "$(printf -- 'paths:\n  - "**/*.go"')" "GO RULE BODY"

  _run_step
  grep -q "GENERAL RULE BODY" "$HOME/.pi/agent/AGENTS.md"
  ! grep -q "GO RULE BODY" "$HOME/.pi/agent/AGENTS.md"
}

@test "a rule scoped away from pi is left out" {
  _rule general.md "" "GENERAL RULE BODY"
  _rule bash-tool.md 'harness: [claude]' "CLAUDE ONLY BODY"

  _run_step
  ! grep -q "CLAUDE ONLY BODY" "$HOME/.pi/agent/AGENTS.md"
}

@test "the context file is written on a machine that has no Claude Code install" {
  # The bug this step's rewrite fixes: Pi's rules used to come from
  # ~/.claude/rules/, which only Claude Code's own sync fills, so a machine
  # running Pi alone got a warning and no context file at all.
  _rule general.md "" "GENERAL RULE BODY"
  _local_rule testing.local.md "" "RUN PYTEST BARE"
  [ ! -e "$HOME/.claude" ]

  _run_step
  [ "$status" -eq 0 ]
  grep -q "GENERAL RULE BODY" "$HOME/.pi/agent/AGENTS.md"
  grep -q "RUN PYTEST BARE" "$HOME/.pi/agent/AGENTS.md"
  [ ! -e "$HOME/.claude" ]
}

@test "a machine-local rule reaches the context file" {
  # A *.local.md exists in no repo — reading the workbench's own rules tree
  # instead of the merged layers is what would silently drop it.
  _rule general.md "" "GENERAL RULE BODY"
  _local_rule testing.local.md "" "RUN PYTEST BARE"

  _run_step
  grep -q "RUN PYTEST BARE" "$HOME/.pi/agent/AGENTS.md"
}

@test "the generated layer reaches the context file" {
  # workbench.md is written by workbench-rules rather than shipped by the repo,
  # and is the other layer a harness reading only the repo sources would lose.
  _rule general.md "" "GENERAL RULE BODY"
  _generated_rule workbench.md "" "MANAGED AT SOME PATH"

  _run_step
  grep -q "MANAGED AT SOME PATH" "$HOME/.pi/agent/AGENTS.md"
}

@test "an override replaces the repo rule it shares a name with" {
  _rule general.md "" "REPO BODY"
  _local_rule general.md "" "OVERRIDE BODY"

  _run_step
  grep -q "OVERRIDE BODY" "$HOME/.pi/agent/AGENTS.md"
  ! grep -q "REPO BODY" "$HOME/.pi/agent/AGENTS.md"
}

@test "an empty paths list is not a scope" {
  # frontmatter_field keeps an inline list's brackets, so `paths: []` reads
  # back as the non-empty string "[]" — path-scoped, on a rule scoped to
  # nothing.
  _rule general.md 'paths: []' "GENERAL RULE BODY"

  _run_step
  [ "$status" -eq 0 ]
  grep -q "GENERAL RULE BODY" "$HOME/.pi/agent/AGENTS.md"
}

@test "a dangling rule symlink is skipped rather than read" {
  # frontmatter_field answers empty for a path that is not there, so a
  # dangling link passes the reaches-Pi predicate and then kills awk — taking
  # the whole sync down with it.
  _rule general.md "" "GENERAL RULE BODY"
  _rule issue-tracker.md "" "TRACKER RULE BODY"
  echo "gone" > "$TMPDIR/deleted.md"
  ln -s "$TMPDIR/deleted.md" "$RULES/dangling.md"
  rm "$TMPDIR/deleted.md"

  _run_step
  [ "$status" -eq 0 ]
  grep -q "GENERAL RULE BODY" "$HOME/.pi/agent/AGENTS.md"
  grep -q "TRACKER RULE BODY" "$HOME/.pi/agent/AGENTS.md"
  [ ! -e "$HOME/.pi/agent/AGENTS.md.tmp" ]
}

@test "frontmatter is stripped from the concatenated body" {
  _rule general.md 'description: irrelevant to pi' "GENERAL RULE BODY"

  _run_step
  ! grep -q "description: irrelevant to pi" "$HOME/.pi/agent/AGENTS.md"
}

@test "the file names its source and its escape hatch" {
  _rule general.md "" "GENERAL RULE BODY"

  _run_step
  grep -q "AGENTS.override.md" "$HOME/.pi/agent/AGENTS.md"
  grep -q "otto-workbench" "$HOME/.pi/agent/AGENTS.md"
  grep -q "$RULES" "$HOME/.pi/agent/AGENTS.md"
}

@test "the preamble comes first" {
  _rule general.md "" "GENERAL RULE BODY"

  _run_step
  run head -30 "$HOME/.pi/agent/AGENTS.md"
  [[ "$output" == *"Agent protocols"* ]]
}

@test "two runs produce a byte-identical file" {
  # Concatenation order must be sorted, not the order bash walks an
  # associative array in — an unsorted walk rewrites the file on every sync and
  # every diff of it is noise.
  _rule general.md "" "A BODY"
  _rule issue-tracker.md "" "B BODY"
  _rule self-review.md "" "C BODY"

  _run_step
  cp "$HOME/.pi/agent/AGENTS.md" "$TMPDIR/first"
  _run_step
  run diff "$TMPDIR/first" "$HOME/.pi/agent/AGENTS.md"
  [ "$status" -eq 0 ]
}

@test "a retired rule leaves the context file on the next run" {
  _rule general.md "" "GENERAL RULE BODY"
  _rule doomed.md "" "DOOMED BODY"
  _run_step
  grep -q "DOOMED BODY" "$HOME/.pi/agent/AGENTS.md"

  rm "$RULES/doomed.md"
  _run_step
  ! grep -q "DOOMED BODY" "$HOME/.pi/agent/AGENTS.md"
}

@test "AGENTS.override.md is never touched" {
  _rule general.md "" "GENERAL RULE BODY"
  mkdir -p "$HOME/.pi/agent"
  echo "MINE" > "$HOME/.pi/agent/AGENTS.override.md"

  _run_step
  [ "$(cat "$HOME/.pi/agent/AGENTS.override.md")" = "MINE" ]
}

@test "no rule at all writes nothing rather than an empty file" {
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.pi/agent/AGENTS.md" ]
  [[ "$output" == *"no rule reaches Pi"* ]]
}

@test "no rule at all leaves a previous good file alone" {
  _rule general.md "" "GENERAL RULE BODY"
  _run_step
  rm "$RULES/general.md"

  _run_step
  grep -q "GENERAL RULE BODY" "$HOME/.pi/agent/AGENTS.md"
}
