#!/usr/bin/env bats
# Tests for bin/migrations/20261023-models-from-env-local.sh — carries the model
# ids ~/.env.local exported into config.yml's models.*.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/bin/migrations/20261023-models-from-env-local.sh"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"
  CONFIG="$TMPDIR/config/config.yml"
  ENV_FILE="$FAKE_HOME/.env.local"
}

teardown() {
  common_teardown
}

# _run_migration — the migration against the sandbox, with the facade loaded as
# the framework loads it: ENV_LOCAL_FILE and WORKBENCH_CONFIG_FILE derive from
# HOME and WORKBENCH_CONFIG_DIR.
_run_migration() {
  bash -c '
    HOME="$3/home"
    export WORKBENCH_CONFIG_DIR="$3/config"
    export WORKBENCH_STATE_DIR="$3/state"
    export WORKBENCH_CACHE_DIR="$3/cache"
    export NO_COLOR=1
    WORKBENCH_DIR="$2"
    . "$2/lib/ui.sh"
    . "$2/lib/migrations.sh"
    . "$1"
    migration_20261023_models_from_env_local
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR"
}

@test "no ~/.env.local defers" {
  run _run_migration
  [ "$status" -eq 4 ]
  [ ! -e "$CONFIG" ]
}

@test "each export moves to its key, and the line goes" {
  printf '%s\n' 'export AI_MODEL=claude-opus-5-5' \
    "export AI_OPUS_MODEL='claude-opus-5-5'" \
    'export AI_SONNET_MODEL="claude-sonnet-5-5"' \
    'export AI_HAIKU_MODEL=claude-haiku-5-5' \
    'export KEEP_ME=1' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  [ "$(yq -o=json -I=0 '.models' "$CONFIG")" = '{"session":"claude-opus-5-5","deep":"claude-opus-5-5","standard":"claude-sonnet-5-5","light":"claude-haiku-5-5"}' ]
  [ "$(cat "$ENV_FILE")" = "export KEEP_ME=1" ]
}

@test "a config.yml the machine never had is created with its modeline" {
  printf 'export AI_HAIKU_MODEL=claude-haiku-5-5\n' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  head -1 "$CONFIG" | grep -q 'yaml-language-server'
  [ "$(yq '.models.light' "$CONFIG")" = "claude-haiku-5-5" ]
}

@test "the pre-rename ANTHROPIC_* names are carried too" {
  printf '%s\n' 'export ANTHROPIC_MODEL=m-session' 'export ANTHROPIC_DEFAULT_OPUS_MODEL=m-deep' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  [ "$(yq '.models.session + " " + .models.deep' "$CONFIG")" = "m-session m-deep" ]
  [ ! -s "$ENV_FILE" ]
}

@test "the AI_* name wins over the ANTHROPIC_* one, which is left for the operator" {
  printf '%s\n' 'export ANTHROPIC_MODEL=old' 'export AI_MODEL=new' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  [ "$(yq '.models.session' "$CONFIG")" = "new" ]
  [ "$(cat "$ENV_FILE")" = "export ANTHROPIC_MODEL=old" ]
}

@test "a key already set keeps its value, and a disagreeing export stays" {
  # A machine that ran 20261024-models-from-agent-model before this existed has
  # every tier filled; a different alias value is the operator's to reconcile.
  mkdir -p "${CONFIG%/*}"
  printf 'models:\n  deep: claude-sonnet-5-5\n  light: claude-haiku-5-5\n' > "$CONFIG"
  printf '%s\n' 'export AI_OPUS_MODEL=claude-opus-5-5' 'export AI_HAIKU_MODEL=claude-haiku-5-5' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  [ "$(yq '.models.deep' "$CONFIG")" = "claude-sonnet-5-5" ]
  # The agreeing line is deleted; the disagreeing one is not.
  [ "$(cat "$ENV_FILE")" = "export AI_OPUS_MODEL=claude-opus-5-5" ]
}

@test "agent.provider is not put in front of a Claude Code id" {
  # The provider the phases ran on need not be the one Claude Code used.
  mkdir -p "${CONFIG%/*}"
  printf 'agent:\n  provider: openai\n  model: gpt-6\n' > "$CONFIG"
  printf 'export AI_HAIKU_MODEL=claude-haiku-5-5\n' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  [ "$(yq '.models.light' "$CONFIG")" = "claude-haiku-5-5" ]
  [ ! -s "$ENV_FILE" ]
}

@test "a slashed export is carried as written, retired, and reported as a change" {
  printf 'export AI_MODEL=openrouter/anthropic-claude-x\n' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  [ "$(yq '.models.session' "$CONFIG")" = "openrouter/anthropic-claude-x" ]
  [ ! -s "$ENV_FILE" ]
}

@test "the agent.model migration after it leaves the lifted tiers alone" {
  # The order this file's date buys: env tiers first, agent.model only fills
  # what they left unset.
  mkdir -p "${CONFIG%/*}"
  printf 'agent:\n  model: claude-opus-5-5\n' > "$CONFIG"
  printf 'export AI_HAIKU_MODEL=claude-haiku-5-5\n' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  run bash -c '
    HOME="$3/home"
    export WORKBENCH_CONFIG_DIR="$3/config" WORKBENCH_STATE_DIR="$3/state" WORKBENCH_CACHE_DIR="$3/cache" NO_COLOR=1
    WORKBENCH_DIR="$2"
    . "$2/lib/ui.sh"
    . "$2/lib/migrations.sh"
    . "$1"
    migration_20261024_models_from_agent_model
  ' _ "$REPO_ROOT/bin/migrations/20261024-models-from-agent-model.sh" "$REPO_ROOT" "$TMPDIR"
  [ "$status" -eq 0 ]
  [ "$(yq -o=json -I=0 '.models' "$CONFIG")" = '{"light":"claude-haiku-5-5","deep":"claude-opus-5-5","standard":"claude-opus-5-5"}' ]
  # run_component_migrations walks "$dir"/*.sh, so glob order is run order.
  local in_order=("$REPO_ROOT"/bin/migrations/*-models-from-*.sh)
  [ "${in_order[0]##*/}" = "20261023-models-from-env-local.sh" ]
}

@test "a tier naming the same model on a provider, with a level, still agrees" {
  # 20261024-models-from-agent-model writes the provider the phases ran on; the
  # export never carried one, so it is the id that has to match.
  mkdir -p "${CONFIG%/*}"
  printf 'models:\n  deep: google-vertex-claude/claude-opus-5-5:high\n' > "$CONFIG"
  printf 'export AI_OPUS_MODEL=claude-opus-5-5\n' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  [ "$(yq '.models.deep' "$CONFIG")" = "google-vertex-claude/claude-opus-5-5:high" ]
  [ ! -s "$ENV_FILE" ]
}

@test "a commented model line is neither carried nor deleted" {
  printf '# export AI_MODEL=claude-opus-5-5\n' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 3 ]
  [ ! -e "$CONFIG" ]
  [ "$(cat "$ENV_FILE")" = "# export AI_MODEL=claude-opus-5-5" ]
}

@test "a second run is a no-op" {
  printf 'export AI_MODEL=claude-opus-5-5\n' > "$ENV_FILE"
  run _run_migration
  [ "$status" -eq 0 ]
  cp "$CONFIG" "$TMPDIR/after-first"
  run _run_migration
  [ "$status" -eq 3 ]
  cmp "$TMPDIR/after-first" "$CONFIG"
}
