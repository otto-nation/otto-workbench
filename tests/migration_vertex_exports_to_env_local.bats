#!/usr/bin/env bats
# Tests for zsh/migrations/20260903-vertex-exports-to-env-local.sh — moves the
# Vertex and Claude Code exports out of ~/.zshrc, which is read after the config
# layers, into ~/.env.local, which is read before them.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/portable.sh
  source "$REPO_ROOT/lib/portable.sh"
  MIGRATION="$REPO_ROOT/zsh/migrations/20260903-vertex-exports-to-env-local.sh"
  FAKE_HOME="$(mktemp -d)"
  ZSHRC="$FAKE_HOME/.zshrc"
  ENV_LOCAL="$FAKE_HOME/.env.local"
}

teardown() {
  rm -rf "$FAKE_HOME"
  common_teardown
}

# Runs the migration against the sandbox files with the ui.sh helpers stubbed
# out. Sources the file and then calls its function, which is what the framework
# does (lib/migrations.sh — _source_migration, then "$fn_name"), and reads the
# exit status the framework reads to decide what to record.
_run_migration() {
  bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    info()    { echo "INFO $*"; }
    err()     { echo "ERR $*" >&2; }
    MIGRATION_NOOP=3
    MIGRATION_DEFERRED=4
    ZSHRC_FILE="$2"
    ENV_LOCAL_FILE="$3"
    . "$4/lib/portable.sh"
    . "$1"
    migration_20260903_vertex_exports_to_env_local
  ' _ "$MIGRATION" "$ZSHRC" "$ENV_LOCAL" "$REPO_ROOT"
}

# _legacy_zshrc — a ~/.zshrc from a machine that exported the routing variables
# below the line that sources the loader.
_legacy_zshrc() {
  cat > "$ZSHRC" <<'EOF'
source "$HOME/.config/zsh/config.d/loader.zsh"

export EDITOR=nvim

export CLAUDE_CODE_USE_VERTEX=1
export CLOUD_ML_REGION=global
export ANTHROPIC_VERTEX_PROJECT_ID=proj-x
export ANTHROPIC_MODEL=claude-opus-5
EOF
}

@test "moves the exports into ~/.env.local" {
  _legacy_zshrc
  printf '%s\n' 'export CONTEXT7_API_KEY=abc' > "$ENV_LOCAL"

  run _run_migration
  [ "$status" -eq 0 ]

  run grep -c '^export ' "$ENV_LOCAL"
  [ "$output" -eq 5 ]
  run grep -qx 'export ANTHROPIC_VERTEX_PROJECT_ID=proj-x' "$ENV_LOCAL"
  [ "$status" -eq 0 ]
}

@test "removes only the moved lines from ~/.zshrc" {
  _legacy_zshrc
  : > "$ENV_LOCAL"

  run _run_migration
  [ "$status" -eq 0 ]

  run grep -c '^export ' "$ZSHRC"
  [ "$output" -eq 1 ]
  run grep -qx 'export EDITOR=nvim' "$ZSHRC"
  [ "$status" -eq 0 ]
  run grep -q 'loader.zsh' "$ZSHRC"
  [ "$status" -eq 0 ]
}

@test "a line already in ~/.env.local is dropped, not duplicated" {
  _legacy_zshrc
  printf '%s\n' 'export CLOUD_ML_REGION=global' > "$ENV_LOCAL"

  run _run_migration
  [ "$status" -eq 0 ]

  run grep -c '^export CLOUD_ML_REGION=' "$ENV_LOCAL"
  [ "$output" -eq 1 ]
  run grep -q '^export CLOUD_ML_REGION=' "$ZSHRC"
  [ "$status" -ne 0 ]
}

@test "a conflicting value keeps the one the shell was using" {
  # ~/.zshrc is read after ~/.env.local, so its value is the one in effect. The
  # shell has to answer the same after the migration as before it.
  _legacy_zshrc
  printf '%s\n' 'export CLOUD_ML_REGION=us-east5' > "$ENV_LOCAL"

  run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"WARN Set in both files"* ]]
  [[ "$output" == *"CLOUD_ML_REGION"* ]]

  run grep -q '^export CLOUD_ML_REGION=' "$ZSHRC"
  [ "$status" -ne 0 ]
  run grep -c '^export CLOUD_ML_REGION=' "$ENV_LOCAL"
  [ "$output" -eq 1 ]
  run grep -qx 'export CLOUD_ML_REGION=global' "$ENV_LOCAL"
  [ "$status" -eq 0 ]
}

@test "a superseded value is commented out, not deleted" {
  _legacy_zshrc
  printf '%s\n' 'export CLOUD_ML_REGION=us-east5' > "$ENV_LOCAL"

  run _run_migration
  [ "$status" -eq 0 ]
  run grep -q '^# superseded by the same export moved out of ~/.zshrc.*us-east5' "$ENV_LOCAL"
  [ "$status" -eq 0 ]
}

@test "the value a conflict resolves to is the one a shell reads last" {
  # Reading the file is the only assertion that settles it — the moved line has
  # to land after the commented-out one, whatever order the migration wrote in.
  _legacy_zshrc
  printf '%s\n' 'export CLOUD_ML_REGION=us-east5' > "$ENV_LOCAL"
  _run_migration

  run bash -c 'set -a; . "$1"; printf "%s" "$CLOUD_ML_REGION"' _ "$ENV_LOCAL"
  [ "$output" = "global" ]
}

@test "the header is written once, not once per run" {
  _legacy_zshrc
  : > "$ENV_LOCAL"
  _run_migration

  printf '%s\n' 'export ANTHROPIC_DEFAULT_HAIKU_MODEL=claude-haiku-4-5' >> "$ZSHRC"
  run _run_migration
  [ "$status" -eq 0 ]

  run grep -c '^# Moved here from ~/.zshrc' "$ENV_LOCAL"
  [ "$output" -eq 1 ]
}

@test "no ~/.env.local yet defers rather than recording a no-op" {
  # step_env_local creates the file later in this same sync; a no-op here would
  # retire the migration against a file it never saw.
  _legacy_zshrc

  run _run_migration
  [ "$status" -eq 4 ]
  run grep -c '^export ' "$ZSHRC"
  [ "$output" -eq 5 ]
}

@test "no ~/.zshrc is a no-op" {
  run _run_migration
  [ "$status" -eq 3 ]
}

@test "a ~/.zshrc with none of the exports is a no-op" {
  printf '%s\n' 'export EDITOR=nvim' > "$ZSHRC"
  : > "$ENV_LOCAL"

  run _run_migration
  [ "$status" -eq 3 ]
  run grep -qx 'export EDITOR=nvim' "$ZSHRC"
  [ "$status" -eq 0 ]
}

@test "a second run is a no-op" {
  _legacy_zshrc
  : > "$ENV_LOCAL"
  _run_migration
  local zshrc_after env_after
  zshrc_after=$(cat "$ZSHRC")
  env_after=$(cat "$ENV_LOCAL")

  run _run_migration
  [ "$status" -eq 3 ]
  [ "$(cat "$ZSHRC")" = "$zshrc_after" ]
  [ "$(cat "$ENV_LOCAL")" = "$env_after" ]
}

@test "preserves the ~/.zshrc file mode" {
  _legacy_zshrc
  : > "$ENV_LOCAL"
  chmod 600 "$ZSHRC"

  run _run_migration
  [ "$status" -eq 0 ]
  run file_mode "$ZSHRC"
  [ "$output" = "600" ]
}
