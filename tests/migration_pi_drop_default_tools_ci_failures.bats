#!/usr/bin/env bats
# Tests for ai/pi/migrations/20261101-pi-drop-default-tools-ci-failures.sh —
# deletes live defaultTools when it is exactly the workbench's old drop.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/pi/migrations/20261101-pi-drop-default-tools-ci-failures.sh"
  LIVE="$TMPDIR/pi/agent/settings.json"
  mkdir -p "$TMPDIR/pi/agent"
}

teardown() {
  common_teardown
}

_run_migration() {
  run bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$3/.unused-legacy"
    PI_SETTINGS_FILE="$3/pi/agent/settings.json"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20261101_pi_drop_default_tools_ci_failures
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR"
}

@test "clears a live file still holding the gh_ci_failures drop" {
  cat > "$LIVE" <<'JSON'
{
  "defaultProvider": "google-vertex-claude",
  "defaultTools": ["-gh_ci_failures"]
}
JSON

  _run_migration
  [ "$status" -eq 0 ]
  run jq -e 'has("defaultTools")' "$LIVE"
  [ "$status" -ne 0 ]
  run jq -r '.defaultProvider' "$LIVE"
  [ "$output" = google-vertex-claude ]
}

@test "leaves an operator-chosen defaultTools array alone" {
  echo '{"defaultTools":["read","write"]}' > "$LIVE"
  before=$(cat "$LIVE")

  _run_migration
  [ "$status" -eq 3 ]
  [ "$(cat "$LIVE")" = "$before" ]
}

@test "is a no-op when defaultTools is already absent" {
  echo '{"defaultProvider":"anthropic"}' > "$LIVE"
  before=$(cat "$LIVE")

  _run_migration
  [ "$status" -eq 3 ]
  [ "$(cat "$LIVE")" = "$before" ]
}

@test "is a no-op without a live settings file" {
  _run_migration
  [ "$status" -eq 3 ]
  [ ! -e "$LIVE" ]
}
