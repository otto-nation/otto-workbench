#!/bin/bash
# Colima runtime setup — sourced by docker/setup.sh, do not run directly.
#
# Restores the docker socket chain broken when Docker Desktop is replaced by Colima.
# /var/run/docker.sock already symlinks to ~/.docker/run/docker.sock (created by Docker Desktop);
# we point that second hop at Colima's socket so all tools find Docker without DOCKER_HOST set.

COLIMA_PROFILE="${COLIMA_PROFILE:-default}"

mkdir -p "$HOME/.docker/run"
ln -sf "$HOME/.colima/$COLIMA_PROFILE/docker.sock" "$HOME/.docker/run/docker.sock"
success "$HOME/.docker/run/docker.sock → $HOME/.colima/$COLIMA_PROFILE/docker.sock"
