#!/usr/bin/env bash
# OrbStack runtime setup — sourced by docker/setup.sh, do not run directly.
#
# OrbStack manages /var/run/docker.sock directly — no socket chain fix needed.
# We clean up any stale Colima socket symlink so DOCKER_HOST doesn't point at
# a non-existent socket.

# `|| return` returns from this sourced file, the way the inline install it
# replaced did: with no Homebrew there is no orb to start and no socket of its
# to reason about, so the rest of the file has nothing to act on.
install_cask orb orbstack OrbStack https://orbstack.dev || return

# Start OrbStack if not running — idempotent, launches instantly.
if ! docker info &>/dev/null; then
  info "Starting OrbStack..."
  orb start
  if docker info &>/dev/null; then
    success "OrbStack started"
  else
    warn "OrbStack failed to start — try 'orb start' manually"
  fi
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
