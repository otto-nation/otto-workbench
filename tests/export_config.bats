#!/usr/bin/env bats
# Tests for _export_claude_config(), _profile_excludes_skill(), and workbench-export.

FAKE_HOME=""
EXPORT_DIR=""
TARBALL_ROOT=""

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  HOME="$FAKE_HOME"
  export WORKBENCH_DIR="$REPO_ROOT"
  export WORKBENCH_STABLE_DIR="$REPO_ROOT"
  export NO_COLOR=1
  export WORKBENCH_SKIP_GENERATE=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"
  # shellcheck source=/dev/null
  source "$REPO_ROOT/ai/claude/steps.sh"

  EXPORT_DIR="$TMPDIR/export"
  _export_claude_config "$EXPORT_DIR" "server"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _build_tarball — builds and extracts a tarball once per test that needs it.
_build_tarball() {
  [[ -n "$TARBALL_ROOT" ]] && return
  local out_dir="$TMPDIR/tarball-output"
  local extract_dir="$TMPDIR/extracted"
  mkdir -p "$out_dir" "$extract_dir"
  "$REPO_ROOT/ai/bin/workbench-export" --version "0.0.1-test" --output "$out_dir" >/dev/null 2>&1
  tar xzf "$out_dir/claude-config-0.0.1-test.tar.gz" -C "$extract_dir"
  TARBALL_ROOT="$extract_dir/claude-config-0.0.1-test"
}

# ─── _profile_excludes_skill ─────────────────────────────────────────────────

@test "_profile_excludes_skill: returns 0 for excluded skill (dream)" {
  _profile_excludes_skill "server" "dream"
}

@test "_profile_excludes_skill: returns 0 for excluded skill (promote)" {
  _profile_excludes_skill "server" "promote"
}

@test "_profile_excludes_skill: returns 0 for excluded skill (machine)" {
  _profile_excludes_skill "server" "machine"
}

@test "_profile_excludes_skill: returns non-zero for included skill" {
  ! _profile_excludes_skill "server" "anatomy"
}

@test "_profile_excludes_skill: returns non-zero for unknown skill" {
  ! _profile_excludes_skill "server" "nonexistent-skill"
}

@test "_profile_excludes_skill: returns non-zero for unknown profile" {
  ! _profile_excludes_skill "nonexistent-profile" "dream"
}

@test "_profile_excludes_skill: returns non-zero when profiles.yml missing" {
  local orig="$AI_SRC_DIR"
  AI_SRC_DIR="$TMPDIR/no-such-dir"
  ! _profile_excludes_skill "server" "dream"
  AI_SRC_DIR="$orig"
}

# ─── _export_claude_config ───────────────────────────────────────────────────

@test "_export_claude_config: creates expected directory structure" {
  [ -d "$EXPORT_DIR/rules" ]
  [ -d "$EXPORT_DIR/agents" ]
  [ -d "$EXPORT_DIR/skills" ]
}

@test "_export_claude_config: copies settings.json as valid JSON" {
  [ -f "$EXPORT_DIR/settings.json" ]
  run jq empty "$EXPORT_DIR/settings.json"
  [ "$status" -eq 0 ]
}

@test "_export_claude_config: copies CLAUDE.md" {
  [ -f "$EXPORT_DIR/CLAUDE.md" ]
  [ -s "$EXPORT_DIR/CLAUDE.md" ]
}

@test "_export_claude_config: copies all rule files" {
  local src_count dest_count
  src_count=$(find "$REPO_ROOT/ai/guidelines/rules" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  dest_count=$(find "$EXPORT_DIR/rules" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  [ "$src_count" -gt 0 ]
  [ "$src_count" -eq "$dest_count" ]
}

@test "_export_claude_config: copies all agent files" {
  local src_count dest_count
  src_count=$(find "$REPO_ROOT/ai/claude/agents" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  dest_count=$(find "$EXPORT_DIR/agents" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  [ "$src_count" -gt 0 ]
  [ "$src_count" -eq "$dest_count" ]
}

@test "_export_claude_config: excludes dream, promote, machine skills" {
  [ ! -d "$EXPORT_DIR/skills/dream" ]
  [ ! -d "$EXPORT_DIR/skills/promote" ]
  [ ! -d "$EXPORT_DIR/skills/machine" ]
}

@test "_export_claude_config: includes non-excluded skills" {
  [ -d "$EXPORT_DIR/skills/anatomy" ]
  [ -d "$EXPORT_DIR/skills/pr-comments" ]
}

@test "_export_claude_config: total skill count matches source minus excluded" {
  local src_count excluded_count dest_count expected
  src_count=$(find "$REPO_ROOT/ai/claude/skills" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
  excluded_count=$(yq '.profiles.server.exclude.skills | length' "$REPO_ROOT/ai/profiles.yml")
  dest_count=$(find "$EXPORT_DIR/skills" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
  expected=$((src_count - excluded_count))
  [ "$dest_count" -eq "$expected" ]
}

@test "_export_claude_config: does not include MCP configs or memory" {
  [ ! -d "$EXPORT_DIR/mcps" ]
  [ ! -d "$EXPORT_DIR/memory" ]
}

@test "_export_claude_config: defaults to server profile when omitted" {
  local dest="$TMPDIR/export-default"
  _export_claude_config "$dest"

  [ ! -d "$dest/skills/dream" ]
  [ ! -d "$dest/skills/machine" ]
  [ -d "$dest/skills/anatomy" ]
}

@test "_export_claude_config: idempotent — second run produces identical output" {
  local dest2="$TMPDIR/export2"
  _export_claude_config "$dest2" "server"

  local diff_output
  diff_output=$(diff -rq "$EXPORT_DIR" "$dest2" 2>&1) || true
  [[ -z "$diff_output" ]]
}

# ─── sync_claude --export ────────────────────────────────────────────────────

@test "sync_claude --export delegates to _export_claude_config" {
  local dest="$TMPDIR/sync-export"
  sync_claude --export "$dest" --profile "server"

  [ -f "$dest/settings.json" ]
  [ -f "$dest/CLAUDE.md" ]
  [ -d "$dest/rules" ]
  [ -d "$dest/agents" ]
  [ -d "$dest/skills" ]
  [ ! -d "$dest/skills/dream" ]
}

@test "sync_claude --export passes profile through" {
  local dest="$TMPDIR/sync-export-profile"
  sync_claude --export "$dest" --profile "server"

  [ ! -d "$dest/skills/promote" ]
  [ -d "$dest/skills/anatomy" ]
}

# ─── workbench-export script ─────────────────────────────────────────────────

@test "workbench-export: --help prints usage" {
  run "$REPO_ROOT/ai/bin/workbench-export" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Usage:"* ]]
  [[ "$output" == *"--version"* ]]
  [[ "$output" == *"--profile"* ]]
  [[ "$output" == *"--output"* ]]
}

@test "workbench-export: fails without version when no manifest exists" {
  local fake_wb="$TMPDIR/no-manifest-workbench"
  mkdir -p "$fake_wb/.github"
  cp -R "$REPO_ROOT/lib" "$fake_wb/lib"
  cp -R "$REPO_ROOT/ai" "$fake_wb/ai"
  rm -f "$fake_wb/.github/.release-please-manifest.json"

  export WORKBENCH_DIR="$fake_wb"
  run "$REPO_ROOT/ai/bin/workbench-export"
  [ "$status" -ne 0 ]
  [[ "$output" == *"Could not determine version"* ]]
}

@test "workbench-export: produces tarball with explicit version" {
  local out_dir="$TMPDIR/tarball-output"
  mkdir -p "$out_dir"
  run "$REPO_ROOT/ai/bin/workbench-export" --version "1.2.3" --output "$out_dir"
  [ "$status" -eq 0 ]
  [ -f "$out_dir/claude-config-1.2.3.tar.gz" ]
}

@test "workbench-export: tarball contains expected structure" {
  _build_tarball
  [ -f "$TARBALL_ROOT/settings.json" ]
  [ -f "$TARBALL_ROOT/CLAUDE.md" ]
  [ -d "$TARBALL_ROOT/rules" ]
  [ -d "$TARBALL_ROOT/agents" ]
  [ -d "$TARBALL_ROOT/skills" ]
}

@test "workbench-export: tarball excludes server-profile skills" {
  _build_tarball
  [ ! -d "$TARBALL_ROOT/skills/dream" ]
  [ ! -d "$TARBALL_ROOT/skills/promote" ]
  [ ! -d "$TARBALL_ROOT/skills/machine" ]
}

@test "workbench-export: tarball includes non-excluded skills" {
  _build_tarball
  [ -d "$TARBALL_ROOT/skills/anatomy" ]
  [ -d "$TARBALL_ROOT/skills/pr-comments" ]
}

@test "workbench-export: reads version from release-please manifest" {
  local out_dir="$TMPDIR/tarball-output"
  local fake_wb="$TMPDIR/fake-workbench"
  mkdir -p "$out_dir" "$fake_wb/.github"

  echo '{".": "4.5.6"}' > "$fake_wb/.github/.release-please-manifest.json"

  cp -R "$REPO_ROOT/lib" "$fake_wb/lib"
  cp -R "$REPO_ROOT/ai" "$fake_wb/ai"

  export WORKBENCH_DIR="$fake_wb"
  run "$REPO_ROOT/ai/bin/workbench-export" --output "$out_dir"
  [ "$status" -eq 0 ]
  [ -f "$out_dir/claude-config-4.5.6.tar.gz" ]
}

@test "workbench-export: prints output path on success" {
  local out_dir="$TMPDIR/tarball-output"
  mkdir -p "$out_dir"
  run "$REPO_ROOT/ai/bin/workbench-export" --version "1.0.0" --output "$out_dir"
  [ "$status" -eq 0 ]
  [[ "$output" == *"claude-config-1.0.0.tar.gz"* ]]
  [[ "$output" == *"profile: server"* ]]
}
