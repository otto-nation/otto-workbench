#!/usr/bin/env bats
# Tests for step_claude_agents rendering an agent's `tier:` into the model and effort Claude Code reads.
# select-on: ai/lib/agent/harness_models.py lib/config_cli.py ai/claude/agents/*.md

setup() {
  load 'test_helper'
  common_setup
  HOME_DIR="$BATS_TEST_TMPDIR/h"
  AGENTS_OUT="$HOME_DIR/.claude/agents"
}

teardown() {
  common_teardown
}

# _seed_config LINE... — writes the sandbox's machine-wide config.yml.
_seed_config() {
  mkdir -p "$HOME_DIR/.config/workbench"
  printf '%s\n' "$@" > "$HOME_DIR/.config/workbench/config.yml"
}

# _override NAME CONTENT — an agent in the operator's override layer.
_override() {
  mkdir -p "$HOME_DIR/.config/workbench/overrides/ai/claude/agents"
  printf '%s\n' "$2" > "$HOME_DIR/.config/workbench/overrides/ai/claude/agents/$1.md"
}

# _agents_run — one step_claude_agents against the sandbox, in its own process
# because constants.sh freezes every path from HOME at source time.
_agents_run() {
  env HOME="$HOME_DIR" WORKBENCH_CONFIG_DIR="$HOME_DIR/.config/workbench" \
    WORKBENCH_STATE_DIR="$HOME_DIR/.local/state/workbench" \
    WORKBENCH_CACHE_DIR="$HOME_DIR/.cache/workbench" WORKBENCH_DIR="$REPO_ROOT" \
    WORKBENCH_STABLE_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -e -c '
      mkdir -p "$HOME"
      . "$WORKBENCH_DIR/lib/ui.sh"
      . "$WORKBENCH_DIR/ai/claude/steps.sh"
      step_claude_agents' _
}

# _frontmatter FILE — the frontmatter lines of FILE, without the fences.
_frontmatter() {
  awk 'NR == 1 && /^---$/ { f = 1; next } f && /^---$/ { exit } f' "$1"
}

@test "an agent runs on its tier's model and level" {
  _seed_config 'models:' '  light: claude-haiku-5-5:minimal' '  deep: anthropic/claude-opus-5-5:xhigh'
  run _agents_run
  [ "$status" -eq 0 ]
  run _frontmatter "$AGENTS_OUT/explain.md"
  [[ "$output" == *$'model: claude-haiku-5-5\neffort: low'* ]]
  [[ "$output" != *"tier:"* ]]
  run _frontmatter "$AGENTS_OUT/reviewer.md"
  [[ "$output" == *$'model: claude-opus-5-5\neffort: xhigh'* ]]
}

@test "a tier with no level sets the model and leaves effort to the session" {
  _seed_config 'models:' '  standard: claude-sonnet-5-5'
  run _agents_run
  [ "$status" -eq 0 ]
  run _frontmatter "$AGENTS_OUT/reviewer-lite.md"
  [[ "$output" == *"model: claude-sonnet-5-5"* ]]
  [[ "$output" != *"effort:"* ]]
}

@test "an unset tier, or one naming no Claude model, inherits the session's model" {
  _seed_config 'models:' '  light: openai/gpt-6-luna'
  run _agents_run
  [ "$status" -eq 0 ]
  run _frontmatter "$AGENTS_OUT/explain.md"
  [[ "$output" == *"model: inherit"* ]]
  run _frontmatter "$AGENTS_OUT/debugger.md"
  [[ "$output" == *"model: inherit"* ]]
}

@test "only the frontmatter is rewritten" {
  _seed_config 'models:' '  deep: claude-opus-5-5'
  run _agents_run
  [ "$status" -eq 0 ]
  diff <(awk 'f; /^---$/ && ++n == 2 { f = 1 }' "$REPO_ROOT/ai/claude/agents/reviewer.md") \
       <(awk 'f; /^---$/ && ++n == 2 { f = 1 }' "$AGENTS_OUT/reviewer.md")
}

@test "an override's own model line yields to its tier" {
  _seed_config 'models:' '  deep: claude-opus-5-5'
  _override mine $'---\nname: mine\ndescription: d\nmodel: haiku\ntier: deep\n---\n\ntier: not frontmatter'
  run _agents_run
  [ "$status" -eq 0 ]
  run cat "$AGENTS_OUT/mine.md"
  [ "$output" = $'---\nname: mine\ndescription: d\nmodel: claude-opus-5-5\n---\n\ntier: not frontmatter' ]
}

@test "an agent with no tier is installed as written" {
  _override plain $'---\nname: plain\ndescription: d\nmodel: sonnet\n---\n\nbody'
  run _agents_run
  [ "$status" -eq 0 ]
  cmp "$HOME_DIR/.config/workbench/overrides/ai/claude/agents/plain.md" "$AGENTS_OUT/plain.md"
}

@test "changing a tier's model reaches the installed agent on the next sync" {
  _seed_config 'models:' '  deep: claude-opus-5-5'
  _agents_run
  _seed_config 'models:' '  deep: claude-opus-5'
  run _agents_run
  [ "$status" -eq 0 ]
  _frontmatter "$AGENTS_OUT/migrate.md" > "$BATS_TEST_TMPDIR/fm"
  grep -qx 'model: claude-opus-5' "$BATS_TEST_TMPDIR/fm"
}
