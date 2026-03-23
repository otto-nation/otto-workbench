#!/bin/bash
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

# ─── Runtime selection ────────────────────────────────────────────────────────

select_runtime() {
  local runtimes=()
  local dir

  # Discover runtimes dynamically — any subdirectory containing setup.sh qualifies
  for dir in "$SCRIPT_DIR"/*/; do
    [[ -f "${dir}setup.sh" ]] && runtimes+=("$(basename "$dir")")
  done

  if [[ ${#runtimes[@]} -eq 0 ]]; then
    err "No runtimes found in $SCRIPT_DIR"
    exit 1
  fi

  info "Which docker runtime are you using?"
  local i=1
  for runtime in "${runtimes[@]}"; do
    echo "  [$i] $runtime"
    i=$(( i + 1 ))
  done
  echo

  local _sel
  select_menu _sel "${#runtimes[@]}" --default skip --single
  [[ -z "$_sel" ]] && { DOCKER_RUNTIME=""; return; }

  DOCKER_RUNTIME="${runtimes[$((_sel - 1))]}"
}

# ─── Main ─────────────────────────────────────────────────────────────────────

echo -e "${BOLD}${BLUE}Docker setup${NC}\n"

select_runtime

if [[ -z "$DOCKER_RUNTIME" ]]; then
  skip "Docker runtime setup"
  exit 0
fi

info "Runtime: $DOCKER_RUNTIME"
# Source the runtime-specific setup so it shares helpers defined above
# shellcheck source=/dev/null
. "$SCRIPT_DIR/$DOCKER_RUNTIME/setup.sh"

# Testcontainers reads ~/.testcontainers.properties before checking DOCKER_HOST, so Gradle
# test runs in non-interactive shells (where aliases-docker.zsh is not sourced) also work.
echo
info "Testcontainers"
install_symlink "$SCRIPT_DIR/testcontainers.properties" ~/.testcontainers.properties

echo
success "Docker setup complete!"
