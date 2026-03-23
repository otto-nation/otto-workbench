#!/bin/bash
# Docker sync steps — re-applies socket setup and testcontainers config non-interactively.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).
#
# Runtime selection is NOT repeated on sync — the active runtime is detected from
# the existing docker.sock symlink target. If no runtime is detected, sync skips
# socket setup and prints a reminder to run docker/setup.sh.
#
# Re-running is safe — install_symlink is idempotent.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_docker_socket — re-applies the docker socket symlink for the detected runtime.
# Detects the active runtime by reading the target of the existing docker.sock symlink.
# Colima: re-symlinks $COLIMA_DIR/<profile>/docker.sock → $DOCKER_RUN_DIR/docker.sock.
# Other runtimes (OrbStack, etc.) manage their own socket — nothing to re-apply.
# No-op with an info message if no runtime is detected.
step_docker_socket() {
  local socket_target
  socket_target=$(readlink "$DOCKER_RUN_DIR/docker.sock" 2>/dev/null || true)

  if [[ "$socket_target" == "$COLIMA_DIR"* ]]; then
    local profile
    profile=$(basename "$(dirname "$socket_target")")
    mkdir -p "$DOCKER_RUN_DIR"
    install_symlink "$COLIMA_DIR/$profile/docker.sock" "$DOCKER_RUN_DIR/docker.sock"
  elif [[ -n "$socket_target" ]]; then
    echo -e "  ${DIM}✓ socket managed externally ($(basename "$(dirname "$socket_target")")${NC})"
  else
    echo -e "  ${DIM}⊘ no docker runtime detected — run docker/setup.sh to configure${NC}"
  fi
}

# step_docker_testcontainers — symlinks testcontainers.properties so Gradle tests
# work in non-interactive shells where docker aliases are not loaded.
step_docker_testcontainers() {
  [[ -f "$TESTCONTAINERS_SRC" ]] || return
  install_symlink "$TESTCONTAINERS_SRC" "$TESTCONTAINERS_FILE"
}

# sync_docker — re-applies socket and testcontainers config non-interactively.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_docker() {
  [[ "$OSTYPE" == "darwin"* ]] || return

  echo; info "docker socket → $DOCKER_RUN_DIR/"
  step_docker_socket

  echo; info "testcontainers → $TESTCONTAINERS_FILE"
  step_docker_testcontainers
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Docker sync${NC}\n"

  sync_docker

  echo
  success "Docker sync complete!"
fi
