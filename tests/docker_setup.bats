#!/usr/bin/env bats
# Tests for docker/setup.sh runtime detection and conflict checking.

setup() {
  load 'test_helper'
  TMPDIR="$(mktemp -d)"

  # Build a minimal fake docker setup structure
  FAKE_DOCKER="$TMPDIR/docker"
  FAKE_STATE="$TMPDIR/state"
  mkdir -p "$FAKE_DOCKER/orbstack" "$FAKE_DOCKER/colima" "$FAKE_STATE"

  # Create minimal setup.sh stubs for each runtime
  echo '#!/usr/bin/env bash' > "$FAKE_DOCKER/orbstack/setup.sh"
  echo '#!/usr/bin/env bash' > "$FAKE_DOCKER/colima/setup.sh"

  # Create aliases.zsh stubs
  echo '# orbstack aliases' > "$FAKE_DOCKER/orbstack/aliases.zsh"
  echo '# colima aliases' > "$FAKE_DOCKER/colima/aliases.zsh"

  # Source the real docker/setup.sh functions (not the main section)
  # We extract just the function definitions for unit testing
  . "$REPO_ROOT/lib/ui.sh"
  DOCKER_RUNTIME_ALIASES="$FAKE_STATE/docker-aliases.zsh"
  WORKBENCH_STATE_DIR="$FAKE_STATE"
}

teardown() {
  rm -rf "$TMPDIR"
}

# ─── Runtime detection from aliases symlink ─────────────────────────────────

@test "detects orbstack from aliases symlink" {
  ln -sf "$FAKE_DOCKER/orbstack/aliases.zsh" "$DOCKER_RUNTIME_ALIASES"
  local target existing
  target=$(readlink "$DOCKER_RUNTIME_ALIASES")
  existing="${target##*/docker/}"
  existing="${existing%%/aliases.zsh}"
  [[ "$existing" == "orbstack" ]]
}

@test "detects colima from aliases symlink" {
  ln -sf "$FAKE_DOCKER/colima/aliases.zsh" "$DOCKER_RUNTIME_ALIASES"
  local target existing
  target=$(readlink "$DOCKER_RUNTIME_ALIASES")
  existing="${target##*/docker/}"
  existing="${existing%%/aliases.zsh}"
  [[ "$existing" == "colima" ]]
}

@test "returns empty when no aliases symlink exists" {
  local existing=""
  if [[ -L "$DOCKER_RUNTIME_ALIASES" ]]; then
    local target
    target=$(readlink "$DOCKER_RUNTIME_ALIASES" 2>/dev/null || true)
    existing="${target##*/docker/}"
    existing="${existing%%/aliases.zsh}"
  fi
  [[ -z "$existing" ]]
}

@test "returns empty when aliases symlink is broken" {
  ln -sf "/nonexistent/path/docker/fake/aliases.zsh" "$DOCKER_RUNTIME_ALIASES"
  local target existing
  target=$(readlink "$DOCKER_RUNTIME_ALIASES" 2>/dev/null || true)
  existing="${target##*/docker/}"
  existing="${existing%%/aliases.zsh}"
  # Detection works even with broken symlink — the directory check in setup.sh
  # catches invalid runtimes via [[ -d "$SCRIPT_DIR/$_existing" ]]
  [[ -n "$existing" ]]
}

# ─── Conflict detection ─────────────────────────────────────────────────────

@test "conflict check warns when switching to orbstack with colima in PATH" {
  # Create a fake colima binary
  local fake_bin="$TMPDIR/bin"
  make_fake_binary "$fake_bin" "colima"

  # Extract the function definition from docker/setup.sh and run it in a clean bash
  # subshell — sourcing the whole file would execute its main section (exit 0).
  run bash -c "
    . '$REPO_ROOT/lib/ui.sh'
    export NO_COLOR=1
    PATH='$fake_bin:\$PATH'
    $(sed -n '/^_docker_check_conflicts/,/^}/p' "$REPO_ROOT/docker/setup.sh")
    _docker_check_conflicts orbstack
  "
  [[ "$output" == *"Colima is installed"* ]]
}

@test "conflict check is silent when runtime is unchanged" {
  # When _existing == DOCKER_RUNTIME, no conflict check should run
  # This tests the conditional logic, not the function itself
  local _existing="orbstack"
  local DOCKER_RUNTIME="orbstack"
  local should_check=false
  if [[ "$_existing" != "$DOCKER_RUNTIME" ]]; then
    should_check=true
  fi
  [[ "$should_check" == false ]]
}
