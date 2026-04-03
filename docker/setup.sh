#!/usr/bin/env bash
# Docker runtime setup.
#
# Usage: bash docker/setup.sh
#        (also called automatically by install.sh)
#
# What it does:
#   1. Prompts for your docker runtime (Colima, OrbStack, etc.)
#   2. Runs runtime-specific socket/daemon setup
#   3. Symlinks testcontainers.properties so Gradle tests work in non-interactive shells
#
# Re-running is safe — existing symlinks are updated silently; real files prompt before overwrite.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"

# ─── Runtime aliases symlink ──────────────────────────────────────────────────

# _docker_write_runtime_aliases RUNTIME
# Creates (or replaces) the runtime marker symlink at DOCKER_RUNTIME_ALIASES.
# The symlink points to docker/<runtime>/aliases.zsh in the workbench.
# Sourced at shell startup by zsh/config.d/aliases/docker.zsh.
_docker_write_runtime_aliases() {
  local runtime="$1"
  local aliases_src="$SCRIPT_DIR/$runtime/aliases.zsh"
  if [[ ! -f "$aliases_src" ]]; then
    warn "No aliases.zsh found for runtime '$runtime' — skipping marker"
    return
  fi
  mkdir -p "$WORKBENCH_STATE_DIR"
  install_symlink "$aliases_src" "$DOCKER_RUNTIME_ALIASES"
}

# ─── Conflict detection ───────────────────────────────────────────────────────

# _docker_check_conflicts RUNTIME
# Warns when switching to OrbStack with Colima still installed/running,
# or switching to Colima with OrbStack still running.
_docker_check_conflicts() {
  local runtime="$1"

  if [[ "$runtime" == "orbstack" ]] && command -v colima &>/dev/null; then
    echo
    warn "Colima is installed — switching to OrbStack."
    echo -e "  ${DIM}The Colima lazy-start wrapper will no longer load for your shell.${NC}"
    if colima status &>/dev/null 2>&1; then
      echo -e "  ${DIM}Colima appears to be running. Stop it to avoid two runtimes competing:${NC}"
      echo -e "  ${DIM}  colima stop${NC}"
    fi
  fi

  if [[ "$runtime" == "colima" ]] && command -v orb &>/dev/null; then
    echo
    warn "OrbStack is installed — switching to Colima."
    echo -e "  ${DIM}Quit OrbStack from the menu bar to avoid socket conflicts.${NC}"
  fi
}

# ─── Runtime selection ────────────────────────────────────────────────────────

select_runtime() {
  local _sel
  select_subdirs _sel "$SCRIPT_DIR" "Which docker runtime are you using?" --default skip --single \
    || exit 1
  DOCKER_RUNTIME="$_sel"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

echo -e "${BOLD}${BLUE}Docker setup${NC}\n"

select_runtime

if [[ -z "$DOCKER_RUNTIME" ]]; then
  skip "Docker runtime setup"
  exit 0
fi

info "Runtime: $DOCKER_RUNTIME"
_docker_check_conflicts "$DOCKER_RUNTIME"

[[ -f "$SCRIPT_DIR/$DOCKER_RUNTIME/setup.sh" ]] \
  || { err "Runtime setup not found: $SCRIPT_DIR/$DOCKER_RUNTIME/setup.sh"; exit 1; }
# Source the runtime-specific setup so it shares helpers defined above
# shellcheck source=/dev/null
. "$SCRIPT_DIR/$DOCKER_RUNTIME/setup.sh"

echo
info "Runtime aliases → $DOCKER_RUNTIME_ALIASES"
_docker_write_runtime_aliases "$DOCKER_RUNTIME"

# Testcontainers reads ~/.testcontainers.properties before checking DOCKER_HOST, so Gradle
# test runs in non-interactive shells (where aliases-docker.zsh is not sourced) also work.
echo
info "Testcontainers"
install_symlink "$SCRIPT_DIR/testcontainers.properties" ~/.testcontainers.properties

echo
success "Docker setup complete!"

# shellcheck source=docker/summary.sh
. "$SCRIPT_DIR/summary.sh"
print_docker_summary "$DOCKER_RUNTIME"
