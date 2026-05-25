#!/usr/bin/env bats
# Tests for the initial state generation migration.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  # Source libs with fake HOME so all constants resolve there
  HOME="$FAKE_HOME"
  export WORKBENCH_DIR="$REPO_ROOT"
  export NO_COLOR=1
  # shellcheck source=/dev/null
  source "$REPO_ROOT/lib/ui.sh"

  # Source the migration file
  # shellcheck source=/dev/null
  source "$REPO_ROOT/bin/migrations/20260422-generate-initial-state.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# ─── Core components ──────────────────────────────────────────────────────────

@test "core components are always installed (no state needed)" {
  migration_20260422_generate_initial_state

  # Core components return 0 regardless of state file
  run state_is_installed "bin"
  [[ "$status" -eq 0 ]]

  run state_is_installed "git"
  [[ "$status" -eq 0 ]]

  run state_is_installed "zsh"
  [[ "$status" -eq 0 ]]
}

# ─── Docker detection ─────────────────────────────────────────────────────────

@test "detects docker by runtime aliases symlink" {
  mkdir -p "$(dirname "$DOCKER_RUNTIME_ALIASES")"
  ln -s "$REPO_ROOT/docker/orbstack/aliases.zsh" "$DOCKER_RUNTIME_ALIASES"

  migration_20260422_generate_initial_state

  run state_is_installed "docker"
  [[ "$status" -eq 0 ]]
}

@test "skips docker when not installed" {
  migration_20260422_generate_initial_state

  run state_is_installed "docker"
  [[ "$status" -ne 0 ]]
}

# ─── Claude detection ─────────────────────────────────────────────────────────

@test "detects claude by settings file" {
  mkdir -p "$(dirname "$CLAUDE_SETTINGS_FILE")"
  echo '{}' > "$CLAUDE_SETTINGS_FILE"

  migration_20260422_generate_initial_state

  run state_is_installed "ai"
  [[ "$status" -eq 0 ]]

  run state_is_installed "ai/claude"
  [[ "$status" -eq 0 ]]
}

# ─── Serena detection ─────────────────────────────────────────────────────────

@test "detects serena by symlink in local bin" {
  mkdir -p "$LOCAL_BIN_DIR"
  ln -s /dev/null "$LOCAL_BIN_DIR/serena-mcp"

  migration_20260422_generate_initial_state

  run state_is_installed "ai"
  [[ "$status" -eq 0 ]]

  run state_is_installed "ai/serena"
  [[ "$status" -eq 0 ]]
}

# ─── Ghostty detection ────────────────────────────────────────────────────────

@test "detects ghostty by config directory" {
  mkdir -p "$GHOSTTY_CONFIG_DIR"

  migration_20260422_generate_initial_state

  run state_is_installed "terminals"
  [[ "$status" -eq 0 ]]

  run state_is_installed "terminals/ghostty"
  [[ "$status" -eq 0 ]]
}

# ─── Zed detection ────────────────────────────────────────────────────────────

@test "detects zed by settings file" {
  mkdir -p "$(dirname "$ZED_SETTINGS_FILE")"
  echo '{}' > "$ZED_SETTINGS_FILE"

  migration_20260422_generate_initial_state

  run state_is_installed "editors"
  [[ "$status" -eq 0 ]]

  run state_is_installed "editors/zed"
  [[ "$status" -eq 0 ]]
}

# ─── Sublime detection ────────────────────────────────────────────────────────

@test "detects sublime by settings file" {
  mkdir -p "$(dirname "$SUBLIME_SETTINGS_FILE")"
  echo '{}' > "$SUBLIME_SETTINGS_FILE"

  migration_20260422_generate_initial_state

  run state_is_installed "editors"
  [[ "$status" -eq 0 ]]

  run state_is_installed "editors/sublime"
  [[ "$status" -eq 0 ]]
}

# ─── Idempotency ──────────────────────────────────────────────────────────────

@test "skips when YAML state file already exists" {
  state_record "docker"

  migration_20260422_generate_initial_state

  # Migration was a no-op — only "docker" should be present
  run state_is_installed "docker"
  [[ "$status" -eq 0 ]]

  # Terminals should not have been detected (ghostty dir absent)
  run state_is_installed "terminals"
  [[ "$status" -ne 0 ]]
}

@test "skips when legacy state file exists" {
  mkdir -p "$(dirname "$INSTALLED_STATE_FILE")"
  echo "something" > "$INSTALLED_STATE_FILE"

  migration_20260422_generate_initial_state

  # Migration was a no-op — YAML file should not exist
  [[ ! -f "$INSTALL_YML_FILE" ]]
}

# ─── Multiple components ──────────────────────────────────────────────────────

@test "detects multiple installed components" {
  # Set up docker
  mkdir -p "$(dirname "$DOCKER_RUNTIME_ALIASES")"
  ln -s /dev/null "$DOCKER_RUNTIME_ALIASES"

  # Set up claude
  mkdir -p "$(dirname "$CLAUDE_SETTINGS_FILE")"
  echo '{}' > "$CLAUDE_SETTINGS_FILE"

  # Set up ghostty
  mkdir -p "$GHOSTTY_CONFIG_DIR"

  migration_20260422_generate_initial_state

  # Detected optional components
  run state_is_installed "docker"
  [[ "$status" -eq 0 ]]
  run state_is_installed "ai"
  [[ "$status" -eq 0 ]]
  run state_is_installed "ai/claude"
  [[ "$status" -eq 0 ]]
  run state_is_installed "terminals"
  [[ "$status" -eq 0 ]]
  run state_is_installed "terminals/ghostty"
  [[ "$status" -eq 0 ]]

  # Not-installed components should be absent
  run state_is_installed "editors"
  [[ "$status" -ne 0 ]]
  run state_is_installed "ai/serena"
  [[ "$status" -ne 0 ]]
}

# ─── YAML migration ──────────────────────────────────────────────────────────

@test "migration converts legacy state file to YAML" {
  # shellcheck source=/dev/null
  source "$REPO_ROOT/bin/migrations/20260524-migrate-state-to-yaml.sh"

  # Create a legacy state file
  mkdir -p "$(dirname "$INSTALLED_STATE_FILE")"
  cat > "$INSTALLED_STATE_FILE" <<'EOF'
ai
ai/claude
docker
terminals
terminals/ghostty
EOF

  migration_20260524_migrate_state_to_yaml

  # YAML should exist with correct content
  [[ -f "$INSTALL_YML_FILE" ]]
  run state_is_installed "ai"
  [[ "$status" -eq 0 ]]
  run state_is_installed "ai/claude"
  [[ "$status" -eq 0 ]]
  run state_is_installed "docker"
  [[ "$status" -eq 0 ]]
  run state_is_installed "terminals"
  [[ "$status" -eq 0 ]]
  run state_is_installed "terminals/ghostty"
  [[ "$status" -eq 0 ]]

  # Old file should be renamed
  [[ ! -f "$INSTALLED_STATE_FILE" ]]
  [[ -f "${INSTALLED_STATE_FILE}.migrated" ]]
}
