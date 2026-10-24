#!/usr/bin/env bats
# Tests for the three 20261024 migrations that carry agent.model and
# agent.provider into models.<tier>: the machine-wide config.yml, the
# container .workbench.yml, and the report on a repo's tracked .workbench.yml.
#
# One file for the three because they share the rewrite — the container half
# sources the machine-wide one for it — and a change to that rewrite is checked
# against every scope it reaches.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATIONS="$REPO_ROOT/bin/migrations"
  CONFIG="$TMPDIR/config.yml"
}

teardown() {
  common_teardown
}

# _run NAME [ARG] — source the named migration file in a framework-shaped shell
# and call its function with ARG, with the ui.sh helpers stubbed out.
_run() {
  local name="$1"
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$3"
    LIB_SRC_DIR="$3/lib"
    BIN_SRC_DIR="$3/bin"
    LEGACY_WORKBENCH_ROOT="$4/.unused-legacy"
    WORKBENCH_CONFIG_FILE="$4/config.yml"
    WORKBENCH_PROJECT_CONFIG_NAME=".workbench.yml"
    . "$WORKBENCH_DIR/lib/gitenv.sh"
    . "$WORKBENCH_DIR/lib/git_layout.sh"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$5/$1.sh"
    fn="migration_${1//-/_}"
    "$fn" "$2"
  ' _ "$name" "${2:-}" "$REPO_ROOT" "$TMPDIR" "$MIGRATIONS"
}

_tier() { yq -r ".models.$2 // \"unset\"" "$1"; }

# ── config.yml ────────────────────────────────────────────────────────────────

@test "no config.yml defers" {
  run _run 20261024-models-from-agent-model
  [ "$status" -eq 4 ]
}

@test "every tier takes agent.model on agent.provider, and both keys go" {
  printf 'agent:\n  backend: pi\n  model: claude-sonnet-5-5\n  provider: anthropic\n' > "$CONFIG"
  run _run 20261024-models-from-agent-model
  [ "$status" -eq 0 ]
  [ "$(_tier "$CONFIG" deep)" = "anthropic/claude-sonnet-5-5" ]
  [ "$(_tier "$CONFIG" standard)" = "anthropic/claude-sonnet-5-5" ]
  [ "$(_tier "$CONFIG" light)" = "anthropic/claude-sonnet-5-5" ]
  [ "$(yq -r '.agent.model // "absent"' "$CONFIG")" = "absent" ]
  [ "$(yq -r '.agent.provider // "absent"' "$CONFIG")" = "absent" ]
  [ "$(yq -r '.agent.backend' "$CONFIG")" = "pi" ]
}

@test "a model with no provider is carried bare" {
  printf 'agent:\n  model: sonnet\n' > "$CONFIG"
  run _run 20261024-models-from-agent-model
  [ "$status" -eq 0 ]
  [ "$(_tier "$CONFIG" light)" = "sonnet" ]
}

@test "a slashed model with no provider is carried, with a warning" {
  printf 'agent:\n  model: anthropic/claude-x\n' > "$CONFIG"
  run _run 20261024-models-from-agent-model
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"contains a slash, which models.* may read as provider/model"* ]]
  [ "$(_tier "$CONFIG" deep)" = "anthropic/claude-x" ]
}

@test "a tier already set keeps its value" {
  printf 'agent:\n  model: claude-opus-5-5\nmodels:\n  light: anthropic/claude-haiku-5-5\n' > "$CONFIG"
  run _run 20261024-models-from-agent-model
  [ "$status" -eq 0 ]
  [ "$(_tier "$CONFIG" light)" = "anthropic/claude-haiku-5-5" ]
  [ "$(_tier "$CONFIG" deep)" = "claude-opus-5-5" ]
}

@test "a provider with no model is warned about and removed" {
  printf 'agent:\n  provider: openai\n' > "$CONFIG"
  run _run 20261024-models-from-agent-model
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"agent.provider 'openai' has no agent.model"* ]]
  [ "$(_tier "$CONFIG" deep)" = "unset" ]
  [ "$(yq -r '.agent.provider // "absent"' "$CONFIG")" = "absent" ]
}

@test "a config naming neither key is a noop" {
  printf 'models:\n  deep: anthropic/claude-opus-5-5\n' > "$CONFIG"
  run _run 20261024-models-from-agent-model
  [ "$status" -eq 3 ]
}

# ── the container file ────────────────────────────────────────────────────────

_seed() {
  SEED="$TMPDIR/seed"
  mkdir -p "$SEED"
  printf 'x\n' > "$SEED/a.txt"
  make_container_seed "$SEED"
  make_worktree_container "$TMPDIR/c" "$SEED"
}

@test "rewrites a container's config even when the machine-wide half is not loaded" {
  # An applied migration is skipped before it is sourced, so the container half
  # must load the shared rewrite itself; _run sources only this file.
  _seed
  printf 'agent:\n  model: claude-sonnet-5-5\n' > "$TMPDIR/c/.workbench.yml"
  run _run 20261024-models-from-agent-model-container "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  [ "$(_tier "$TMPDIR/c/.workbench.yml" standard)" = "claude-sonnet-5-5" ]
  [ "$(yq -r '.agent.model // "absent"' "$TMPDIR/c/.workbench.yml")" = "absent" ]
}

@test "a container model takes the machine-wide provider when it names none" {
  # The merged config ran the container's model on the global provider.
  _seed
  printf 'agent:\n  provider: openrouter\n' > "$CONFIG"
  printf 'agent:\n  model: anthropic/claude-x\n' > "$TMPDIR/c/.workbench.yml"
  run _run 20261024-models-from-agent-model-container "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  [ "$(_tier "$TMPDIR/c/.workbench.yml" deep)" = "openrouter/anthropic/claude-x" ]
}

@test "a container migrated after the machine-wide half takes each tier's provider" {
  # The repo was registered later: config.yml has no agent.provider left, but its
  # tiers now carry the provider the merged config ran every model on.
  _seed
  printf 'models:\n  deep: google-vertex-claude/claude-opus-5\n  standard: google-vertex-claude/claude-sonnet-5\n  light: arn:aws:x/abc\n' > "$CONFIG"
  printf 'agent:\n  model: claude-sonnet-5\n' > "$TMPDIR/c/.workbench.yml"
  run _run 20261024-models-from-agent-model-container "$TMPDIR/c/main"
  [ "$status" -eq 0 ]
  [ "$(_tier "$TMPDIR/c/.workbench.yml" deep)" = "google-vertex-claude/claude-sonnet-5" ]
  [ "$(_tier "$TMPDIR/c/.workbench.yml" standard)" = "google-vertex-claude/claude-sonnet-5" ]
  # A first segment that is not provider-shaped names no provider, as parse_ref reads it.
  [ "$(_tier "$TMPDIR/c/.workbench.yml" light)" = "claude-sonnet-5" ]
}

@test "the container half leaves a worktree's tracked config alone" {
  _seed
  printf 'agent:\n  model: claude-sonnet-5-5\n' > "$TMPDIR/c/main/.workbench.yml"
  run _run 20261024-models-from-agent-model-container "$TMPDIR/c/main"
  [ "$status" -eq 3 ]
  [ "$(yq -r '.agent.model' "$TMPDIR/c/main/.workbench.yml")" = "claude-sonnet-5-5" ]
}

@test "an ordinary clone has no container to rewrite" {
  _seed
  git clone -q "$SEED" "$TMPDIR/plain"
  printf 'agent:\n  model: x\n' > "$TMPDIR/plain/.workbench.yml"
  run _run 20261024-models-from-agent-model-container "$TMPDIR/plain"
  [ "$status" -eq 3 ]
  [ "$(yq -r '.agent.model' "$TMPDIR/plain/.workbench.yml")" = "x" ]
}

# ── a repo's tracked file ─────────────────────────────────────────────────────

@test "a repo's .workbench.yml naming agent.model is reported, not rewritten" {
  mkdir -p "$TMPDIR/repo"
  printf 'agent:\n  model: claude-sonnet-5-5\n' > "$TMPDIR/repo/.workbench.yml"
  run _run 20261024-warn-agent-model-project "$TMPDIR/repo"
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN"*"still names agent.model or agent.provider"* ]]
  [ "$(yq -r '.agent.model' "$TMPDIR/repo/.workbench.yml")" = "claude-sonnet-5-5" ]
}

@test "a repo's .workbench.yml naming neither key is a noop" {
  mkdir -p "$TMPDIR/repo"
  printf 'models:\n  deep: x\n' > "$TMPDIR/repo/.workbench.yml"
  run _run 20261024-warn-agent-model-project "$TMPDIR/repo"
  [ "$status" -eq 3 ]
  [[ "$output" != *"WARN"* ]]
}
