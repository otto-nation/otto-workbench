#!/usr/bin/env bats
# Tests for ai/pi/migrations/20261020-pi-extensions-drop-linear.sh — swaps the
# plain pi-extensions entry an earlier sync wrote for the template's filtered one.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  MIGRATION="$REPO_ROOT/ai/pi/migrations/20261020-pi-extensions-drop-linear.sh"
  LIVE="$TMPDIR/pi/agent/settings.json"
  TEMPLATE="$TMPDIR/template.json"
  mkdir -p "$TMPDIR/pi/agent"
  cat > "$TEMPLATE" <<'JSON'
{
  "packages": [
    { "source": "git:github.com/usemaximum/pi-extensions", "extensions": ["!extensions/linear/**"] },
    { "source": "git:github.com/obra/superpowers@v6.3.0", "extensions": [] }
  ]
}
JSON
}

teardown() {
  common_teardown
}

# Same harness as migration_superpowers_filter_extension.bats: ui.sh helpers
# stubbed, the real lib/migrations.sh sourced for MIGRATION_NOOP, and the
# function's own exit status reported.
_run_migration() {
  run bash -c '
    success() { echo "OK $*"; }
    warn()    { echo "WARN $*"; }
    WORKBENCH_DIR="$2"
    LIB_SRC_DIR="$2/lib"
    LEGACY_WORKBENCH_ROOT="$3/.unused-legacy"
    PI_SETTINGS_FILE="$3/pi/agent/settings.json"
    PI_SETTINGS_SRC="$4"
    . "$WORKBENCH_DIR/lib/migrations.sh"
    . "$1"
    migration_20261020_pi_extensions_drop_linear
  ' _ "$MIGRATION" "$REPO_ROOT" "$TMPDIR" "$TEMPLATE"
}

@test "replaces the plain entry with the template's filtered one" {
  cat > "$LIVE" <<'JSON'
{
  "defaultProvider": "google-vertex-claude",
  "packages": [
    "git:github.com/usemaximum/pi-extensions",
    { "source": "git:github.com/obra/superpowers@v6.3.0", "extensions": [] },
    "npm:something-the-operator-added"
  ]
}
JSON

  _run_migration
  [ "$status" -eq 0 ]
  run jq -c '.packages' "$LIVE"
  [ "$output" = '[{"source":"git:github.com/usemaximum/pi-extensions","extensions":["!extensions/linear/**"]},{"source":"git:github.com/obra/superpowers@v6.3.0","extensions":[]},"npm:something-the-operator-added"]' ]
  run jq -r '.defaultProvider' "$LIVE"
  [ "$output" = google-vertex-claude ]
}

@test "replaces a pinned plain entry too" {
  # Identity ignores the ref, as Pi's does, so a pinned entry is the same
  # package and still loads the extension.
  echo '{"packages":["git:github.com/usemaximum/pi-extensions@v1.2.3"]}' > "$LIVE"

  _run_migration
  [ "$status" -eq 0 ]
  run jq -c '.packages[0].extensions' "$LIVE"
  [ "$output" = '["!extensions/linear/**"]' ]
  run jq -r '.packages[0].source' "$LIVE"
  [ "$output" = 'git:github.com/usemaximum/pi-extensions@v1.2.3' ]
}

@test "no warning when an object entry already excludes linear among other filters" {
  echo '{"packages":[{"source":"git:github.com/usemaximum/pi-extensions","extensions":["!extensions/x/**","!extensions/linear/**"]}]}' > "$LIVE"

  _run_migration
  [ "$status" -eq 3 ]
  [[ "$output" != *WARN* ]]
}

@test "leaves an object entry with the operator's own filters alone" {
  echo '{"packages":[{"source":"git:github.com/usemaximum/pi-extensions","skills":[]}]}' > "$LIVE"
  before=$(cat "$LIVE")

  _run_migration
  [ "$status" -eq 3 ]
  [[ "$output" == *WARN* ]]
  [ "$(cat "$LIVE")" = "$before" ]
}

@test "is a no-op once the entry matches the template" {
  echo '{"packages":[{"extensions":["!extensions/linear/**"],"source":"git:github.com/usemaximum/pi-extensions"}]}' > "$LIVE"

  _run_migration
  [ "$status" -eq 3 ]
  [[ "$output" != *WARN* ]]
}

@test "is a no-op when pi-extensions is not installed" {
  echo '{"packages":[{"source":"git:github.com/obra/superpowers@v6.3.0","extensions":[]}]}' > "$LIVE"
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

@test "the shipped template carries the filtered entry this migration installs" {
  # The migration copies whatever the template declares; with no object entry
  # there it would silently do nothing on every machine.
  run jq -c '[.packages[] | select(type == "object" and (.source | startswith("git:github.com/usemaximum/pi-extensions")))][0].extensions' \
    "$REPO_ROOT/ai/pi/settings.json"
  [ "$output" = '["!extensions/linear/**"]' ]
}
