#!/usr/bin/env bats
# Tests for which Docker socket the workbench points at: the Linux sync in
# docker/steps.sh, and the DOCKER_HOST export in zsh/config.d/aliases/docker.zsh.

setup() {
  load 'test_helper'
  common_setup
  export HOME="$TMPDIR/home"
  mkdir -p "$HOME"
  unset DOCKER_HOST TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE TESTCONTAINERS_HOST_OVERRIDE
  unset WORKBENCH_STATE_DIR XDG_STATE_HOME

  . "$REPO_ROOT/lib/ui.sh"
  . "$REPO_ROOT/docker/steps.sh"
  DOCKER_RUN_DIR="$HOME/.docker/run"
  export XDG_RUNTIME_DIR="$TMPDIR/xdg"
  mkdir -p "$XDG_RUNTIME_DIR" "$TMPDIR/sys"
  # shellcheck disable=SC2034  # read by docker/steps.sh
  DOCKER_SYSTEM_SOCKETS=("$TMPDIR/sys/docker.sock")
}

teardown() {
  common_teardown
}

# make_socket PATH — binds a Unix socket at PATH. Binds by basename from inside
# the directory: the bats scratch path can exceed sun_path's 104-byte limit.
make_socket() {
  python3 -c '
import os, socket, sys
os.chdir(os.path.dirname(sys.argv[1]))
socket.socket(socket.AF_UNIX).bind(os.path.basename(sys.argv[1]))
' "$1"
}

# ─── _detect_native_docker_sock ─────────────────────────────────────────────

@test "a rootless daemon's socket wins over a writable system socket" {
  make_socket "$XDG_RUNTIME_DIR/docker.sock"
  make_socket "$TMPDIR/sys/docker.sock"
  local sock
  _detect_native_docker_sock sock
  [[ "$sock" == "$XDG_RUNTIME_DIR/docker.sock" ]]
}

@test "a writable system socket is used when there is no rootless daemon" {
  make_socket "$TMPDIR/sys/docker.sock"
  local sock
  _detect_native_docker_sock sock
  [[ "$sock" == "$TMPDIR/sys/docker.sock" ]]
}

# passes-at-base: the base ignored DOCKER_SYSTEM_SOCKETS, so it found nothing either; the -w guard is held by the sync tests
@test "a system socket the user cannot write is not used" {
  [[ "$(id -u)" != 0 ]] || skip "root writes any socket"
  make_socket "$TMPDIR/sys/docker.sock"
  chmod 000 "$TMPDIR/sys/docker.sock"
  local sock=unset
  _detect_native_docker_sock sock
  [[ -z "$sock" ]]
}

# ─── _sync_docker_linux ─────────────────────────────────────────────────────

# sync_linux — runs the Linux sync with the steps that write outside $HOME stubbed.
sync_linux() {
  # shellcheck disable=SC2329  # called by _sync_docker_linux
  step_docker_testcontainers() { :; }
  # shellcheck disable=SC2329  # called by _sync_docker_linux
  sync_component_bin() { :; }
  _sync_docker_linux
}

@test "sync repoints a link to an unwritable system socket at the rootless one" {
  [[ "$(id -u)" != 0 ]] || skip "root writes any socket"
  make_socket "$TMPDIR/sys/docker.sock"
  chmod 000 "$TMPDIR/sys/docker.sock"
  make_socket "$XDG_RUNTIME_DIR/docker.sock"
  mkdir -p "$DOCKER_RUN_DIR"
  ln -s "$TMPDIR/sys/docker.sock" "$DOCKER_RUN_DIR/docker.sock"

  run sync_linux
  [[ "$status" -eq 0 ]]
  [[ "$(readlink "$DOCKER_RUN_DIR/docker.sock")" == "$XDG_RUNTIME_DIR/docker.sock" ]]
}

@test "sync removes a link to an unwritable system socket when nothing is usable" {
  [[ "$(id -u)" != 0 ]] || skip "root writes any socket"
  make_socket "$TMPDIR/sys/docker.sock"
  chmod 000 "$TMPDIR/sys/docker.sock"
  mkdir -p "$DOCKER_RUN_DIR"
  ln -s "$TMPDIR/sys/docker.sock" "$DOCKER_RUN_DIR/docker.sock"

  run sync_linux
  [[ "$status" -eq 0 ]]
  [[ ! -L "$DOCKER_RUN_DIR/docker.sock" ]]
}

# passes-at-base: guards the removal this change adds against reaching links it did not make
@test "sync leaves a link it did not make alone when nothing is usable" {
  mkdir -p "$DOCKER_RUN_DIR"
  ln -s "$TMPDIR/elsewhere.sock" "$DOCKER_RUN_DIR/docker.sock"

  run sync_linux
  [[ "$status" -eq 0 ]]
  [[ "$(readlink "$DOCKER_RUN_DIR/docker.sock")" == "$TMPDIR/elsewhere.sock" ]]
}

# ─── docker.zsh ─────────────────────────────────────────────────────────────

# zsh_env OSTYPE — sources docker.zsh in zsh with OSTYPE forced and prints the
# three variables it may export.
zsh_env() {
  zsh -fc 'OSTYPE=$1; source "$2"; print -r -- "DH=$DOCKER_HOST TC=$TESTCONTAINERS_DOCKER_SOCKET_OVERRIDE TH=$TESTCONTAINERS_HOST_OVERRIDE"' \
    _ "$1" "$REPO_ROOT/zsh/config.d/aliases/docker.zsh"
}

@test "docker.zsh keeps a DOCKER_HOST already in the environment" {
  mkdir -p "$DOCKER_RUN_DIR"
  ln -s "$TMPDIR/sys/docker.sock" "$DOCKER_RUN_DIR/docker.sock"
  make_socket "$TMPDIR/sys/docker.sock"
  export DOCKER_HOST="unix:///run/user/3001/docker.sock"

  run zsh_env linux-gnu
  [[ "$status" -eq 0 ]]
  [[ "$output" == "DH=unix:///run/user/3001/docker.sock TC= TH=" ]]
}

@test "docker.zsh points an unset DOCKER_HOST at the canonical link" {
  mkdir -p "$DOCKER_RUN_DIR"
  make_socket "$TMPDIR/sys/docker.sock"
  ln -s "$TMPDIR/sys/docker.sock" "$DOCKER_RUN_DIR/docker.sock"

  run zsh_env linux-gnu
  [[ "$status" -eq 0 ]]
  [[ "$output" == "DH=unix://$HOME/.docker/run/docker.sock TC= TH=" ]]
}

# passes-at-base: asserts the macOS behaviour this change was careful not to break
@test "docker.zsh sets the testcontainers overrides on macOS" {
  run zsh_env darwin23.0
  [[ "$status" -eq 0 ]]
  [[ "$output" == "DH= TC=/var/run/docker.sock TH=localhost" ]]
}
