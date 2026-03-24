#!/bin/bash
# OrbStack runtime setup — sourced by docker/setup.sh, do not run directly.
#
# OrbStack manages /var/run/docker.sock directly — no socket chain fix needed.

if ! command -v orb >/dev/null 2>&1; then
  require_command brew "Homebrew not found — install OrbStack manually: https://orbstack.dev" || return
  info "Installing orbstack..."
  brew install --cask orbstack
  success "orbstack installed"
fi

success "OrbStack manages the docker socket automatically"
