#!/usr/bin/env bash
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
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

# ─── Steps ────────────────────────────────────────────────────────────────────

# _docker_detect_runtime — detects the active runtime from the docker.sock symlink target.
# Prints "colima", "orbstack", or "" (unknown/unset).
_docker_detect_runtime() {
  local socket_target
  socket_target=$(readlink "$DOCKER_RUN_DIR/docker.sock" 2>/dev/null || true)
  if [[ "$socket_target" == "$COLIMA_DIR"* ]]; then
    echo "colima"
  elif [[ "$socket_target" == *"orbstack"* ]]; then
    echo "orbstack"
  else
    echo ""
  fi
}

# step_docker_socket — re-applies the docker socket symlink for the detected runtime.
# Detects the active runtime by reading the target of the existing docker.sock symlink.
# Colima: re-symlinks $COLIMA_DIR/<profile>/docker.sock → $DOCKER_RUN_DIR/docker.sock.
# OrbStack: removes stale Colima symlinks — OrbStack manages its own socket.
# No-op with an info message if no runtime is detected.
step_docker_socket() {
  local socket_target
  socket_target=$(readlink "$DOCKER_RUN_DIR/docker.sock" 2>/dev/null || true)

  if [[ "$socket_target" == "$COLIMA_DIR"* ]]; then
    # Check if OrbStack is actually the intended runtime (stale Colima symlink)
    if [[ -f "$DOCKER_RUNTIME_ALIASES" ]] \
       && [[ "$(readlink "$DOCKER_RUNTIME_ALIASES" 2>/dev/null)" == *"orbstack"* ]]; then
      rm -f "$DOCKER_RUN_DIR/docker.sock"
      [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}✓ removed stale Colima socket (OrbStack is active runtime)${NC}" || true
    else
      local profile
      profile=$(basename "$(dirname "$socket_target")")
      mkdir -p "$DOCKER_RUN_DIR"
      install_symlink "$COLIMA_DIR/$profile/docker.sock" "$DOCKER_RUN_DIR/docker.sock"
    fi
  elif [[ -n "$socket_target" ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}✓ socket managed externally ($(basename "$(dirname "$socket_target")")${NC})" || true
  else
    [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}⊘ no docker runtime detected — run docker/setup.sh to configure${NC}" || true
  fi
}

# step_docker_runtime_aliases — restores the runtime marker symlink from socket detection.
# Ensures the zsh runtime-specific aliases file stays in sync with the active runtime.
# No-op if the runtime cannot be detected (e.g. docker was never configured).
step_docker_runtime_aliases() {
  local runtime
  runtime=$(_docker_detect_runtime)
  [[ -z "$runtime" ]] && return

  local aliases_src="$DOCKER_SRC_DIR/$runtime/aliases.zsh"
  [[ -f "$aliases_src" ]] || return

  mkdir -p "$WORKBENCH_STATE_DIR"
  install_symlink "$aliases_src" "$DOCKER_RUNTIME_ALIASES"
}

# step_docker_testcontainers — symlinks testcontainers.properties so Gradle tests
# work in non-interactive shells where docker aliases are not loaded.
step_docker_testcontainers() {
  [[ -f "$TESTCONTAINERS_SRC" ]] || return
  install_symlink "$TESTCONTAINERS_SRC" "$TESTCONTAINERS_FILE"
}

# _detect_native_docker_sock VAR — sets VAR to the Docker socket this user can
# reach, or empty string. A rootless daemon's socket ($XDG_RUNTIME_DIR/docker.sock)
# comes first: a user who has one runs their own daemon on purpose. The system
# daemon's socket counts only when writable — without the docker group it is
# there but refuses every call, and pointing DOCKER_HOST at it turns a missing
# daemon into "permission denied".
_detect_native_docker_sock() {
  local -n __sock=$1
  __sock=""
  local rootless="${XDG_RUNTIME_DIR:-/run/user/$UID}/docker.sock"
  if [[ -S "$rootless" ]]; then
    __sock="$rootless"
    return 0
  fi
  local candidate
  for candidate in "${DOCKER_SYSTEM_SOCKETS[@]}"; do
    if [[ -S "$candidate" && -w "$candidate" ]]; then
      __sock="$candidate"
      return 0
    fi
  done
  return 0
}

# _remove_unusable_docker_link — removes the canonical symlink when it points at a
# system daemon socket this user cannot write. Earlier syncs linked it without
# checking access, and the zsh config exports DOCKER_HOST from it.
# Only links to DOCKER_SYSTEM_SOCKETS are touched (a missing target also counts as
# unwritable); a stale link to a vanished rootless socket is deliberately left alone.
_remove_unusable_docker_link() {
  local link="$DOCKER_RUN_DIR/docker.sock" target candidate
  target=$(readlink "$link" 2>/dev/null || true)
  [[ -n "$target" ]] || return 0
  for candidate in "${DOCKER_SYSTEM_SOCKETS[@]}"; do
    if [[ "$target" == "$candidate" && ! -w "$target" ]]; then
      rm -f "$link"
      [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}✓ removed $link → $target (not writable by $USER)${NC}" || true
      return 0
    fi
  done
  return 0
}

# _sync_docker_linux — detects native Docker socket and symlinks for path consistency.
_sync_docker_linux() {
  sync_header "docker socket → $DOCKER_RUN_DIR/"
  local native_sock
  _detect_native_docker_sock native_sock

  if [[ -n "$native_sock" ]]; then
    mkdir -p "$DOCKER_RUN_DIR"
    install_symlink "$native_sock" "$DOCKER_RUN_DIR/docker.sock"
  else
    _remove_unusable_docker_link
    [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}⊘ no usable Docker socket found — is Docker installed, and is $USER in the docker group?${NC}" || true
  fi

  sync_header "testcontainers → $TESTCONTAINERS_FILE"
  step_docker_testcontainers

  sync_header "docker scripts → $LOCAL_BIN_DIR/"
  sync_component_bin "$DOCKER_SRC_DIR"
}

# sync_docker — re-applies socket, runtime aliases, and testcontainers config non-interactively.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_docker() {
  if [[ "$OSTYPE" == "darwin"* ]]; then
    sync_header "docker socket → $DOCKER_RUN_DIR/"
    step_docker_socket

    sync_header "runtime aliases → $DOCKER_RUNTIME_ALIASES"
    step_docker_runtime_aliases

    sync_header "testcontainers → $TESTCONTAINERS_FILE"
    step_docker_testcontainers

    sync_header "docker scripts → $LOCAL_BIN_DIR/"
    sync_component_bin "$DOCKER_SRC_DIR"
  else
    _sync_docker_linux
  fi
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Docker sync${NC}\n"

  sync_docker

  echo
  success "Docker sync complete!"
fi
