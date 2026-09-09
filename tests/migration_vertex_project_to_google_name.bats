#!/usr/bin/env bats
# Tests for zsh/migrations/20260909-vertex-project-to-google-name.sh — renames
# ANTHROPIC_VERTEX_PROJECT_ID to GOOGLE_CLOUD_PROJECT in ~/.env.local, which is
# the name gcloud, the Google SDKs and Pi all read a project id from.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/zsh/migrations/20260909-vertex-project-to-google-name.sh"
  FAKE_HOME="$(mktemp -d)"
  ENV_LOCAL="$FAKE_HOME/.env.local"
}

teardown() {
  rm -rf "$FAKE_HOME"
  common_teardown
}

# Runs the migration against the sandbox file with the ui.sh helpers stubbed
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
    ENV_LOCAL_FILE="$2"
    . "$1"
    migration_20260909_vertex_project_to_google_name
  ' _ "$MIGRATION" "$ENV_LOCAL"
}

@test "renames an active export" {
  cat > "$ENV_LOCAL" <<'EOF'
export CLAUDE_CODE_USE_VERTEX=1
export ANTHROPIC_VERTEX_PROJECT_ID=proj-x
export CLOUD_ML_REGION=global
EOF
  run _run_migration
  [ "$status" -eq 0 ]
  grep -qx 'export GOOGLE_CLOUD_PROJECT=proj-x' "$ENV_LOCAL"
  run grep -c ANTHROPIC_VERTEX_PROJECT_ID "$ENV_LOCAL"
  [ "$status" -ne 0 ]
}

@test "leaves the region alone" {
  # CLOUD_ML_REGION is where the Anthropic models are provisioned, not where
  # Google's are served. Renaming it would foreclose a machine running Claude on
  # a regional endpoint and Gemini on the global one.
  cat > "$ENV_LOCAL" <<'EOF'
export ANTHROPIC_VERTEX_PROJECT_ID=proj-x
export CLOUD_ML_REGION=us-east5
EOF
  run _run_migration
  [ "$status" -eq 0 ]
  grep -qx 'export CLOUD_ML_REGION=us-east5' "$ENV_LOCAL"
}

@test "renames the commented catalogue line" {
  # The reference line the old template carried, so it matches what the
  # registries generate now.
  cat > "$ENV_LOCAL" <<'EOF'
# export ANTHROPIC_VERTEX_PROJECT_ID=
EOF
  run _run_migration
  [ "$status" -eq 0 ]
  grep -qx '# export GOOGLE_CLOUD_PROJECT=' "$ENV_LOCAL"
}

@test "is a noop on a file already in shape" {
  cat > "$ENV_LOCAL" <<'EOF'
export GOOGLE_CLOUD_PROJECT=proj-x
EOF
  run _run_migration
  [ "$status" -eq 3 ]
  grep -qx 'export GOOGLE_CLOUD_PROJECT=proj-x' "$ENV_LOCAL"
}

@test "is idempotent" {
  cat > "$ENV_LOCAL" <<'EOF'
export ANTHROPIC_VERTEX_PROJECT_ID=proj-x
EOF
  run _run_migration
  [ "$status" -eq 0 ]
  cp "$ENV_LOCAL" "$BATS_TEST_TMPDIR/first"
  run _run_migration
  [ "$status" -eq 3 ]
  diff "$BATS_TEST_TMPDIR/first" "$ENV_LOCAL"
}

@test "stops when both names are set" {
  # Picking one silently repoints either vertex_quota.py or every gcloud call in
  # the shell, depending which way it guessed.
  cat > "$ENV_LOCAL" <<'EOF'
export ANTHROPIC_VERTEX_PROJECT_ID=proj-old
export GOOGLE_CLOUD_PROJECT=proj-new
EOF
  run _run_migration
  [ "$status" -eq 3 ]
  [[ "$output" == *"WARN"* ]]
  grep -qx 'export ANTHROPIC_VERTEX_PROJECT_ID=proj-old' "$ENV_LOCAL"
  grep -qx 'export GOOGLE_CLOUD_PROJECT=proj-new' "$ENV_LOCAL"
}

@test "defers when there is no ~/.env.local yet" {
  # Deferred rather than noop: step_env_local creates the file later in this same
  # sync, and 20260903-vertex-exports-to-env-local can append the old name to it
  # on a later pass. A noop is recorded and never revisited, so this migration
  # would never see those lines.
  run _run_migration
  [ "$status" -eq 4 ]
}
