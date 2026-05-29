#!/usr/bin/env bats
# Tests for _export_claude_config(), _profile_excludes_skill(), and workbench-export.

FAKE_HOME=""

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
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# ─── profiles.yml ────────────────────────────────────────────────────────────

@test "profiles.yml is valid YAML" {
  run yq '.' "$REPO_ROOT/ai/profiles.yml"
  [ "$status" -eq 0 ]
}

@test "profiles.yml has a server profile" {
  run yq -e '.profiles.server' "$REPO_ROOT/ai/profiles.yml"
  [ "$status" -eq 0 ]
}

@test "profiles.yml server profile excludes skills as a list" {
  run yq -e '.profiles.server.exclude.skills | type' "$REPO_ROOT/ai/profiles.yml"
  [ "$status" -eq 0 ]
  [[ "$output" == *"!!seq"* ]]
}

@test "profiles.yml server profile excludes dream skill" {
  run yq -e '.profiles.server.exclude.skills[] | select(. == "dream")' "$REPO_ROOT/ai/profiles.yml"
  [ "$status" -eq 0 ]
}

@test "profiles.yml server profile excludes promote skill" {
  run yq -e '.profiles.server.exclude.skills[] | select(. == "promote")' "$REPO_ROOT/ai/profiles.yml"
  [ "$status" -eq 0 ]
}

@test "profiles.yml server profile excludes machine skill" {
  run yq -e '.profiles.server.exclude.skills[] | select(. == "machine")' "$REPO_ROOT/ai/profiles.yml"
  [ "$status" -eq 0 ]
}

@test "profiles.yml server profile excludes all MCPs" {
  local val
  val=$(yq '.profiles.server.exclude.mcps' "$REPO_ROOT/ai/profiles.yml")
  [[ "$val" == "all" ]]
}

@test "profiles.yml server profile excludes all plugins" {
  local val
  val=$(yq '.profiles.server.exclude.plugins' "$REPO_ROOT/ai/profiles.yml")
  [[ "$val" == "all" ]]
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
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  [ -d "$dest/rules" ]
  [ -d "$dest/agents" ]
  [ -d "$dest/skills" ]
}

@test "_export_claude_config: copies settings.json" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  [ -f "$dest/settings.json" ]
  run jq empty "$dest/settings.json"
  [ "$status" -eq 0 ]
}

@test "_export_claude_config: copies CLAUDE.md" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  [ -f "$dest/CLAUDE.md" ]
  [ -s "$dest/CLAUDE.md" ]
}

@test "_export_claude_config: copies all rule files" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  local src_count dest_count
  src_count=$(find "$REPO_ROOT/ai/guidelines/rules" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  dest_count=$(find "$dest/rules" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  [ "$src_count" -gt 0 ]
  [ "$src_count" -eq "$dest_count" ]
}

@test "_export_claude_config: copies all agent files" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  local src_count dest_count
  src_count=$(find "$REPO_ROOT/ai/claude/agents" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  dest_count=$(find "$dest/agents" -maxdepth 1 -name '*.md' -type f | wc -l | tr -d ' ')
  [ "$src_count" -gt 0 ]
  [ "$src_count" -eq "$dest_count" ]
}

@test "_export_claude_config: excludes dream skill with server profile" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  [ ! -d "$dest/skills/dream" ]
}

@test "_export_claude_config: excludes promote skill with server profile" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  [ ! -d "$dest/skills/promote" ]
}

@test "_export_claude_config: excludes machine skill with server profile" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  [ ! -d "$dest/skills/machine" ]
}

@test "_export_claude_config: includes non-excluded skills" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  [ -d "$dest/skills/anatomy" ]
  [ -d "$dest/skills/pr-review" ]
}

@test "_export_claude_config: total skill count matches source minus excluded" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  local src_count excluded_count dest_count expected
  src_count=$(find "$REPO_ROOT/ai/claude/skills" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
  excluded_count=$(yq '.profiles.server.exclude.skills | length' "$REPO_ROOT/ai/profiles.yml")
  dest_count=$(find "$dest/skills" -mindepth 1 -maxdepth 1 -type d | wc -l | tr -d ' ')
  expected=$((src_count - excluded_count))
  [ "$dest_count" -eq "$expected" ]
}

@test "_export_claude_config: uses base settings without user overrides" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  local src_checksum dest_checksum
  src_checksum=$(md5 -q "$REPO_ROOT/ai/claude/settings.json" 2>/dev/null || md5sum "$REPO_ROOT/ai/claude/settings.json" | awk '{print $1}')
  dest_checksum=$(md5 -q "$dest/settings.json" 2>/dev/null || md5sum "$dest/settings.json" | awk '{print $1}')
  [[ "$src_checksum" == "$dest_checksum" ]]
}

@test "_export_claude_config: uses base CLAUDE.md without user overrides" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  local src_checksum dest_checksum
  src_checksum=$(md5 -q "$REPO_ROOT/ai/claude/CLAUDE.md" 2>/dev/null || md5sum "$REPO_ROOT/ai/claude/CLAUDE.md" | awk '{print $1}')
  dest_checksum=$(md5 -q "$dest/CLAUDE.md" 2>/dev/null || md5sum "$dest/CLAUDE.md" | awk '{print $1}')
  [[ "$src_checksum" == "$dest_checksum" ]]
}

@test "_export_claude_config: does not include MCP configs" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  [ ! -d "$dest/mcps" ]
}

@test "_export_claude_config: does not include memory" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest" "server"

  [ ! -d "$dest/memory" ]
}

@test "_export_claude_config: defaults to server profile when omitted" {
  local dest="$TMPDIR/export"
  _export_claude_config "$dest"

  [ ! -d "$dest/skills/dream" ]
  [ ! -d "$dest/skills/machine" ]
  [ -d "$dest/skills/anatomy" ]
}

@test "_export_claude_config: idempotent — second run produces identical output" {
  local dest1="$TMPDIR/export1"
  local dest2="$TMPDIR/export2"
  _export_claude_config "$dest1" "server"
  _export_claude_config "$dest2" "server"

  local diff_output
  diff_output=$(diff -rq "$dest1" "$dest2" 2>&1) || true
  [[ -z "$diff_output" ]]
}

# ─── sync_claude --export ────────────────────────────────────────────────────

@test "sync_claude --export delegates to _export_claude_config" {
  local dest="$TMPDIR/sync-export"
  export WORKBENCH_SYNC=true
  sync_claude --export "$dest" --profile "server"

  [ -f "$dest/settings.json" ]
  [ -f "$dest/CLAUDE.md" ]
  [ -d "$dest/rules" ]
  [ -d "$dest/agents" ]
  [ -d "$dest/skills" ]
  [ ! -d "$dest/skills/dream" ]
}

@test "sync_claude --export with custom profile" {
  local dest="$TMPDIR/sync-export-custom"
  export WORKBENCH_SYNC=true
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
  export WORKBENCH_DIR="$REPO_ROOT"
  run "$REPO_ROOT/ai/bin/workbench-export" --version "1.2.3" --output "$out_dir"
  [ "$status" -eq 0 ]
  [ -f "$out_dir/claude-config-1.2.3.tar.gz" ]
}

@test "workbench-export: tarball contains expected structure" {
  local out_dir="$TMPDIR/tarball-output"
  mkdir -p "$out_dir"
  export WORKBENCH_DIR="$REPO_ROOT"
  "$REPO_ROOT/ai/bin/workbench-export" --version "0.0.1-test" --output "$out_dir" >/dev/null 2>&1

  local extract_dir="$TMPDIR/extracted"
  mkdir -p "$extract_dir"
  tar xzf "$out_dir/claude-config-0.0.1-test.tar.gz" -C "$extract_dir"

  local root="$extract_dir/claude-config-0.0.1-test"
  [ -f "$root/settings.json" ]
  [ -f "$root/CLAUDE.md" ]
  [ -d "$root/rules" ]
  [ -d "$root/agents" ]
  [ -d "$root/skills" ]
}

@test "workbench-export: tarball excludes server-profile skills" {
  local out_dir="$TMPDIR/tarball-output"
  mkdir -p "$out_dir"
  export WORKBENCH_DIR="$REPO_ROOT"
  "$REPO_ROOT/ai/bin/workbench-export" --version "0.0.1-test" --output "$out_dir" >/dev/null 2>&1

  local extract_dir="$TMPDIR/extracted"
  mkdir -p "$extract_dir"
  tar xzf "$out_dir/claude-config-0.0.1-test.tar.gz" -C "$extract_dir"

  local root="$extract_dir/claude-config-0.0.1-test"
  [ ! -d "$root/skills/dream" ]
  [ ! -d "$root/skills/promote" ]
  [ ! -d "$root/skills/machine" ]
}

@test "workbench-export: tarball includes non-excluded skills" {
  local out_dir="$TMPDIR/tarball-output"
  mkdir -p "$out_dir"
  export WORKBENCH_DIR="$REPO_ROOT"
  "$REPO_ROOT/ai/bin/workbench-export" --version "0.0.1-test" --output "$out_dir" >/dev/null 2>&1

  local extract_dir="$TMPDIR/extracted"
  mkdir -p "$extract_dir"
  tar xzf "$out_dir/claude-config-0.0.1-test.tar.gz" -C "$extract_dir"

  local root="$extract_dir/claude-config-0.0.1-test"
  [ -d "$root/skills/anatomy" ]
  [ -d "$root/skills/pr-review" ]
}

@test "workbench-export: reads version from release-please manifest" {
  local out_dir="$TMPDIR/tarball-output"
  local fake_wb="$TMPDIR/fake-workbench"
  mkdir -p "$out_dir" "$fake_wb/.github"

  echo '{"": "4.5.6"}' > "$fake_wb/.github/.release-please-manifest.json"

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
  export WORKBENCH_DIR="$REPO_ROOT"
  run "$REPO_ROOT/ai/bin/workbench-export" --version "1.0.0" --output "$out_dir"
  [ "$status" -eq 0 ]
  [[ "$output" == *"claude-config-1.0.0.tar.gz"* ]]
  [[ "$output" == *"profile: server"* ]]
}
