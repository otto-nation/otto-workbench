#!/usr/bin/env bats
# Tests for bin/migrations/20261021-agent-model-from-tier-env.sh — carries the
# model phases used to dispatch (the sonnet tier) into agent.model.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/bin/migrations/20261021-agent-model-from-tier-env.sh"
  CONFIG="$TMPDIR/config.yml"
  ENV_FILE="$TMPDIR/.env.local"
}

teardown() {
  common_teardown
}

# _run_migration [BACKEND] — AI_BACKEND for the run (empty: unset).
_run_migration() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    info()    { echo "INFO $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$3/.unused-legacy"
    ENV_LOCAL_FILE="$3/.env.local"
    WORKBENCH_CONFIG_FILE="$3/config.yml"
    if [[ -n "$4" ]]; then export AI_BACKEND="$4"; else unset AI_BACKEND; fi
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20261021_agent_model_from_tier_env
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR" "${1:-}"
}

@test "no config.yml defers" {
  run _run_migration pi
  [ "$status" -eq 4 ]
}

@test "carries AI_SONNET_MODEL into an unset agent.model" {
  printf 'agent:\n  provider: google-vertex-claude\n' > "$CONFIG"
  printf 'export AI_SONNET_MODEL=claude-sonnet-5-5\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "claude-sonnet-5-5" ]
  [ "$(yq '.agent.provider' "$CONFIG")" = "google-vertex-claude" ]
}

@test "falls back to the pre-rename name, quotes stripped" {
  printf 'agent: {}\n' > "$CONFIG"
  printf 'export ANTHROPIC_DEFAULT_SONNET_MODEL="claude-sonnet-4-5"\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "claude-sonnet-4-5" ]
}

@test "the current name wins over the pre-rename one" {
  printf 'agent: {}\n' > "$CONFIG"
  printf 'export ANTHROPIC_DEFAULT_SONNET_MODEL=old\nexport AI_SONNET_MODEL=new\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "new" ]
}

@test "a commented export is ignored" {
  printf 'agent: {}\n' > "$CONFIG"
  printf '# export AI_SONNET_MODEL=claude-sonnet-5-5\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 3 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "null" ]
  [[ "$output" == *"WARN"*"agent.model"* ]]
}

@test "an agent.model already set is left alone" {
  printf 'agent:\n  model: grok-4.7\n' > "$CONFIG"
  printf 'export AI_SONNET_MODEL=claude-sonnet-5-5\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 3 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "grok-4.7" ]
}

@test "Claude backend with no tier value keeps the sonnet alias it ran on" {
  printf 'agent: {}\n' > "$CONFIG"
  : > "$ENV_FILE"
  run _run_migration claude
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "sonnet" ]
}

@test "no AI_BACKEND reads agent.backend, defaulting to Claude" {
  printf 'agent:\n  backend: pi\n' > "$CONFIG"
  : > "$ENV_FILE"
  run _run_migration ""
  [ "$status" -eq 3 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "null" ]
  printf 'agent: {}\n' > "$CONFIG"
  run _run_migration ""
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "sonnet" ]
}

@test "Pi: tier words in agent.model and phase models become their exported ids" {
  printf 'agent:\n  model: sonnet\n  phases:\n    scout:\n      model: haiku\n    group:\n      model: opus\n' > "$CONFIG"
  printf 'export AI_SONNET_MODEL=claude-sonnet-5-5\nexport AI_HAIKU_MODEL=claude-haiku-4-5\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "claude-sonnet-5-5" ]
  [ "$(yq '.agent.phases.scout.model' "$CONFIG")" = "claude-haiku-4-5" ]
  [ "$(yq '.agent.phases.group.model' "$CONFIG")" = "opus" ]
  [[ "$output" == *"WARN agent.phases.group.model"* ]]
}

@test "Claude: tier words stay, since the claude CLI resolves them" {
  printf 'agent:\n  model: opus\n' > "$CONFIG"
  printf 'export AI_OPUS_MODEL=claude-opus-5-5\n' > "$ENV_FILE"
  run _run_migration claude
  [ "$status" -eq 3 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "opus" ]
}

@test "a second run changes nothing" {
  printf 'agent: {}\n' > "$CONFIG"
  printf 'export AI_SONNET_MODEL=claude-sonnet-5-5\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 0 ]
  run _run_migration pi
  [ "$status" -eq 3 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "claude-sonnet-5-5" ]
}

@test "reads a bare KEY=value line, and an indented export" {
  printf 'agent: {}\n' > "$CONFIG"
  printf 'AI_SONNET_MODEL=claude-sonnet-5-5\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "claude-sonnet-5-5" ]
  printf 'agent: {}\n' > "$CONFIG"
  printf '   export   AI_SONNET_MODEL=claude-sonnet-5\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "claude-sonnet-5" ]
}

@test "an inline comment is not part of the value; a quoted value keeps its spaces" {
  printf 'agent: {}\n' > "$CONFIG"
  printf 'export AI_SONNET_MODEL=claude-sonnet-5-5   # pinned for vertex\n' > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "claude-sonnet-5-5" ]
  printf 'agent: {}\n' > "$CONFIG"
  printf "export AI_SONNET_MODEL='odd model' # comment\n" > "$ENV_FILE"
  run _run_migration pi
  [ "$status" -eq 0 ]
  [ "$(yq '.agent.model' "$CONFIG")" = "odd model" ]
}
