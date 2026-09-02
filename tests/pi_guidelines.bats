#!/usr/bin/env bats
# Tests for step_pi_guidelines — the generator that turns the rules directory
# Claude loads into the single context file Pi loads.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME/.claude/rules"
  RULES="$HOME/.claude/rules"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _rule NAME FRONTMATTER BODY — writes a rule into the installed rules dir.
# FRONTMATTER is the block's inner lines, or empty for a file with none.
_rule() {
  local name="$1" fm="$2" body="$3"
  if [[ -n "$fm" ]]; then
    printf -- '---\n%s\n---\n%s\n' "$fm" "$body" > "$RULES/$name"
  else
    printf -- '%s\n' "$body" > "$RULES/$name"
  fi
}

_run_step() {
  run bash -c '
    HOME="$2"
    . "$1/lib/ui.sh"
    . "$1/ai/pi/steps.sh"
    step_pi_guidelines
  ' _ "$REPO_ROOT" "$HOME"
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

@test "a machine-local rule reaches the context file" {
  # The case that reading the repo sources instead would silently drop: a
  # *.local.md exists nowhere but the installed directory.
  _rule testing.local.md "" "RUN PYTEST BARE"

  _run_step
  grep -q "RUN PYTEST BARE" "$HOME/.pi/agent/AGENTS.md"
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
}

@test "the preamble comes first" {
  _rule general.md "" "GENERAL RULE BODY"

  _run_step
  run head -30 "$HOME/.pi/agent/AGENTS.md"
  [[ "$output" == *"Agent protocols"* ]]
}

@test "two runs produce a byte-identical file" {
  # Concatenation order must be sorted, not glob order — an unsorted map walk
  # rewrites the file on every sync and every diff of it is noise.
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

@test "an absent rules directory writes nothing rather than an empty file" {
  rm -rf "$HOME/.claude/rules"

  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$HOME/.pi/agent/AGENTS.md" ]
  [[ "$output" == *"no rules"* ]]
}

@test "an absent rules directory leaves a previous good file alone" {
  _rule general.md "" "GENERAL RULE BODY"
  _run_step
  rm -rf "$HOME/.claude/rules"

  _run_step
  grep -q "GENERAL RULE BODY" "$HOME/.pi/agent/AGENTS.md"
}
