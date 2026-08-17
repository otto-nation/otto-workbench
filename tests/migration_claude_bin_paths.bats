#!/usr/bin/env bats
# Tests for ai/claude/migrations/20260806-claude-bin-hook-paths.sh — rewrites
# ~/.claude/settings.json script paths from ~/.claude/bin to ~/.local/bin.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/portable.sh
  source "$REPO_ROOT/lib/portable.sh"
  MIGRATION="$REPO_ROOT/ai/claude/migrations/20260806-claude-bin-hook-paths.sh"
  FAKE_HOME="$(mktemp -d)"
  mkdir -p "$FAKE_HOME/.claude"
  SETTINGS="$FAKE_HOME/.claude/settings.json"
}

teardown() {
  rm -rf "$FAKE_HOME"
  common_teardown
}

# Runs the migration against FAKE_HOME with the ui.sh helpers stubbed out.
# Sources the file and then calls its function, which is what the framework
# does (lib/migrations.sh — _source_migration, then "$fn_name"). The file used
# to invoke itself and this harness leaned on that; the call ran on the
# sourcing pass, outside the `if` that turns a failure into warn-and-retry, so
# a non-zero return there took the whole sync down (#731). Exit status is the
# function's own, which is what the framework reads to decide whether to
# record the migration as applied.
_run_migration() {
  HOME="$FAKE_HOME" bash -c '
    success() { echo "OK $*"; }
    err()     { echo "ERR $*" >&2; }
    . "$1"
    migration_20260806_claude_bin_hook_paths
  ' _ "$MIGRATION"
}

@test "rewrites the statusline path sync cannot reach" {
  cat > "$SETTINGS" <<'JSON'
{"statusLine":{"type":"command","command":"$HOME/.claude/bin/workbench-statusline"}}
JSON
  run _run_migration
  [ "$status" -eq 0 ]
  run jq -r '.statusLine.command' "$SETTINGS"
  [ "$output" = '$HOME/.local/bin/workbench-statusline' ]
}

@test "rewrites every hook command referencing the old dir" {
  cat > "$SETTINGS" <<'JSON'
{"hooks":{"Stop":[{"matcher":"","hooks":[
  {"type":"command","command":"python3 $HOME/.claude/bin/reuse-mode-tracker || true"},
  {"type":"command","command":"python3 $HOME/.claude/bin/ceiling-scan || true"}
]}]}}
JSON
  run _run_migration
  [ "$status" -eq 0 ]
  run grep -c '\$HOME/\.local/bin/' "$SETTINGS"
  [ "$output" -eq 2 ]
  run grep -c '\$HOME/\.claude/bin/' "$SETTINGS"
  [ "$status" -ne 0 ]
}

@test "leaves skills and log paths alone" {
  cat > "$SETTINGS" <<'JSON'
{"hooks":{"Stop":[{"matcher":"","hooks":[
  {"type":"command","command":"bash $HOME/.claude/skills/dream/should-dream.sh >> $HOME/.claude/logs/x"}
]}]}}
JSON
  run _run_migration
  [ "$status" -eq 0 ]
  run jq -r '.hooks.Stop[0].hooks[0].command' "$SETTINGS"
  [ "$output" = 'bash $HOME/.claude/skills/dream/should-dream.sh >> $HOME/.claude/logs/x' ]
}

@test "is idempotent — a second run is a no-op" {
  cat > "$SETTINGS" <<'JSON'
{"statusLine":{"command":"$HOME/.claude/bin/workbench-statusline"}}
JSON
  _run_migration
  local after_first
  after_first=$(cat "$SETTINGS")

  run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"already point at"* ]]
  [ "$(cat "$SETTINGS")" = "$after_first" ]
}

@test "preserves the original file mode" {
  cat > "$SETTINGS" <<'JSON'
{"statusLine":{"command":"$HOME/.claude/bin/workbench-statusline"}}
JSON
  chmod 600 "$SETTINGS"

  run _run_migration
  [ "$status" -eq 0 ]
  run file_mode "$SETTINGS"
  [ "$output" = "600" ]
}

@test "no-op when there is no settings file" {
  run _run_migration
  [ "$status" -eq 0 ]
  [[ "$output" == *"No Claude settings"* ]]
  [ ! -f "$SETTINGS" ]
}

@test "leaves the file untouched when the rewrite would break JSON" {
  # A settings file that is already invalid JSON must not be replaced by an
  # equally invalid rewrite — the migration validates before moving it in.
  printf '%s' '{"statusLine":{"command":"$HOME/.claude/bin/workbench-statusline"' > "$SETTINGS"
  local before
  before=$(cat "$SETTINGS")

  run _run_migration
  [ "$status" -ne 0 ]
  [[ "$output" == *"invalid JSON"* ]]
  [ "$(cat "$SETTINGS")" = "$before" ]
}
