#!/usr/bin/env bash
# OrbStack runtime setup — sourced by docker/setup.sh, do not run directly.
#
# OrbStack manages /var/run/docker.sock directly — no socket chain fix needed.
# We clean up any stale Colima socket symlink so DOCKER_HOST doesn't point at
# a non-existent socket.

if ! command -v orb >/dev/null 2>&1; then
  require_command brew "Homebrew not found — install OrbStack manually: https://orbstack.dev" || return
  info "Installing orbstack..."
  brew install --cask orbstack
  success "orbstack installed"
fi

# Remove stale Colima socket symlink — OrbStack doesn't need it and a dangling
# symlink breaks DOCKER_HOST for all tooling.
if [[ -L "$DOCKER_RUN_DIR/docker.sock" ]]; then
  _orb_target=$(readlink "$DOCKER_RUN_DIR/docker.sock" 2>/dev/null || true)
  if [[ "$_orb_target" == "$COLIMA_DIR"* ]]; then
    rm -f "$DOCKER_RUN_DIR/docker.sock"
    info "Removed stale Colima socket symlink"
  fi
  unset _orb_target
fi

success "OrbStack manages the docker socket automatically"
