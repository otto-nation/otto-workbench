#!/usr/bin/env bash
# Colima runtime setup — sourced by docker/setup.sh, do not run directly.
#
# Restores the docker socket chain broken when Docker Desktop is replaced by Colima.
# /var/run/docker.sock already symlinks to ~/.docker/run/docker.sock (created by Docker Desktop);
# we point that second hop at Colima's socket so all tools find Docker without DOCKER_HOST set.

if ! command -v colima >/dev/null 2>&1; then
  require_command brew "Homebrew not found — install colima manually: https://github.com/abiosoft/colima" || return
  info "Installing colima..."
  brew install colima
  success "colima installed"
fi

COLIMA_PROFILE="${COLIMA_PROFILE:-default}"

mkdir -p "$DOCKER_RUN_DIR"
# The socket won't exist until colima is started; the symlink will dangle until then — expected.
if [[ ! -S "$COLIMA_DIR/$COLIMA_PROFILE/docker.sock" ]]; then
  warn "Colima socket not found — run 'colima start' after setup to create it"
fi
install_symlink "$COLIMA_DIR/$COLIMA_PROFILE/docker.sock" "$DOCKER_RUN_DIR/docker.sock"
