#!/usr/bin/env bats
# Tests for zsh/env.sh — the one definition of the environment a workbench tool
# needs (tool directories on PATH, ~/.env.local), read by the loader, sync,
# maintenance and herdr-app's remote preamble. Runs under `sh`, which is dash on
# CI, because dash is what runs the remote preamble on a Linux host.
bats_require_minimum_version 1.5.0
# select-on: zsh/env.sh

setup() {
  load 'test_helper'
  common_setup
  FAKE_HOME="$BATS_TEST_TMPDIR/home"
  SHIMS="$FAKE_HOME/.local/share/mise/shims"
  mkdir -p "$FAKE_HOME/.local/bin" "$FAKE_HOME/.pi/agent/bin" "$SHIMS"
  # Resolved before any case narrows PATH, so the shell under test is the one
  # the runner's PATH names (dash on CI) rather than whatever /bin/sh is.
  SH="$(command -v sh)"
}

teardown() {
  common_teardown
}

# _in_sh PATH SCRIPT — runs SCRIPT under sh with env.sh sourced, a clean
# environment, HOME at FAKE_HOME and the given starting PATH.
_in_sh() {
  local start_path="$1" script="$2"
  env -i HOME="$FAKE_HOME" PATH="$start_path" "$SH" -c ". \"$REPO_ROOT/zsh/env.sh\"; $script"
}

# The listed directories that exist on this host outside FAKE_HOME (Homebrew),
# so expectations can name them without assuming the host has them.
_host_brew() {
  local d out=""
  for d in /opt/homebrew/bin /home/linuxbrew/.linuxbrew/bin; do
    [[ -d "$d" ]] && out="${out:+$out:}$d"
  done
  printf '%s' "$out"
}

@test "workbench_path puts ~/.local/bin, Pi's bin, mise shims, then Homebrew ahead of the inherited PATH" {
  local brew expected
  brew="$(_host_brew)"
  expected="$FAKE_HOME/.local/bin:$FAKE_HOME/.pi/agent/bin:$SHIMS${brew:+:$brew}:/usr/bin:/bin"

  run _in_sh /usr/bin:/bin 'workbench_path; printf "%s" "$PATH"'

  [ "$status" -eq 0 ]
  [ "$output" = "$expected" ]
}

@test "workbench_path moves a listed directory already on PATH instead of duplicating it" {
  run _in_sh "/usr/bin:$SHIMS:/bin:$FAKE_HOME/.local/bin" 'workbench_path; workbench_path; printf "%s" "$PATH"'

  [ "$status" -eq 0 ]
  [[ "$output" == "$FAKE_HOME/.local/bin:$FAKE_HOME/.pi/agent/bin:$SHIMS:"* ]]
  [[ "$output" == *":/usr/bin:/bin" ]]
  [ "$(grep -o "$SHIMS" <<<"$output" | wc -l | tr -d ' ')" -eq 1 ]
  [ "$(grep -o "$FAKE_HOME/.local/bin" <<<"$output" | wc -l | tr -d ' ')" -eq 1 ]
}

@test "workbench_path skips a listed directory that does not exist" {
  rm -rf "$FAKE_HOME/.pi" "$FAKE_HOME/.local/share"

  run _in_sh /usr/bin:/bin 'workbench_path; printf "%s" "$PATH"'

  [ "$status" -eq 0 ]
  [[ "$output" == "$FAKE_HOME/.local/bin:"* ]]
  [[ "$output" != *"/.pi/"* ]]
  [[ "$output" != *"mise/shims"* ]]
}

@test "workbench_path honours MISE_DATA_DIR for the shims directory" {
  mkdir -p "$BATS_TEST_TMPDIR/mise-data/shims"

  run env -i HOME="$FAKE_HOME" PATH=/usr/bin:/bin MISE_DATA_DIR="$BATS_TEST_TMPDIR/mise-data" \
    "$SH" -c ". \"$REPO_ROOT/zsh/env.sh\"; workbench_path; printf '%s' \"\$PATH\""

  [ "$status" -eq 0 ]
  [[ "$output" == *":$BATS_TEST_TMPDIR/mise-data/shims:"* ]]
  [[ "$output" != *"$SHIMS"* ]]
}

@test "a mise-shimmed tool resolves after workbench_path from a system PATH" {
  printf '#!/bin/sh\necho shim-ran\n' > "$SHIMS/wt"
  chmod +x "$SHIMS/wt"

  run _in_sh /usr/bin:/bin 'workbench_path; wt'

  [ "$status" -eq 0 ]
  [ "$output" = "shim-ran" ]
}

@test "workbench_env exports ~/.env.local assignments to a child process" {
  printf 'GOOGLE_CLOUD_PROJECT=from-env-local\n' > "$FAKE_HOME/.env.local"

  run _in_sh /usr/bin:/bin 'workbench_env; sh -c "printf %s \"\$GOOGLE_CLOUD_PROJECT\""'

  [ "$status" -eq 0 ]
  [ "$output" = "from-env-local" ]
}

@test "workbench_env leaves allexport off afterwards" {
  printf 'A=1\n' > "$FAKE_HOME/.env.local"

  run _in_sh /usr/bin:/bin 'workbench_env; LATER=leaked; sh -c "printf %s \"\${LATER:-unset}\""'

  [ "$status" -eq 0 ]
  [ "$output" = "unset" ]
}

@test "workbench_env skips a broken ~/.env.local with a warning, returns 0, and keeps the PATH" {
  printf 'if then\n' > "$FAKE_HOME/.env.local"

  run --separate-stderr _in_sh /usr/bin:/bin 'workbench_env; echo "status=$?"; printf "%s\n" "${PATH%%:*}"'

  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "status=0" ]
  [ "${lines[1]}" = "$FAKE_HOME/.local/bin" ]
  [[ "$stderr" == *"$FAKE_HOME/.env.local failed to load"* ]]
}

@test "workbench_env loads assignments once when ~/.env.local's last command returns non-zero" {
  printf 'echo x >> "$HOME/runs"\nEARLY=kept\n[ -f /nonexistent ] && export LATE=1\n' > "$FAKE_HOME/.env.local"

  run _in_sh /usr/bin:/bin 'workbench_env; sh -c "printf %s \"\$EARLY\""; echo; wc -l < "$HOME/runs"'

  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "kept" ]
  [ "$(tr -d ' ' <<<"${lines[1]}")" = "1" ]
}

@test "under set -e, workbench_env survives a failing command inside ~/.env.local and restores errexit" {
  printf 'false\nAFTER=reached\n' > "$FAKE_HOME/.env.local"

  run _in_sh /usr/bin:/bin 'set -e; workbench_env; printf "%s\n" "$AFTER"; case "$-" in *e*) echo errexit-on ;; esac'

  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "reached" ]
  [ "${lines[1]}" = "errexit-on" ]
}

@test "loader.zsh applies env.sh in an interactive zsh before any layer" {
  require_zsh || return 1
  mkdir -p "$FAKE_HOME/.config/zsh/config.d"
  cp "$REPO_ROOT/zsh/env.sh" "$FAKE_HOME/.config/zsh/env.sh"
  cp "$REPO_ROOT/zsh/config.d/loader.zsh" "$FAKE_HOME/.config/zsh/config.d/loader.zsh"
  printf 'FROM_ENV_LOCAL=yes\n' > "$FAKE_HOME/.env.local"

  run env -i HOME="$FAKE_HOME" PATH=/usr/bin:/bin zsh -f -i -c \
    'source "$HOME/.config/zsh/config.d/loader.zsh"; print -r -- "${PATH%%:*}"; sh -c "printf %s \"\$FROM_ENV_LOCAL\""'

  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "$FAKE_HOME/.local/bin" ]
  [ "${lines[1]}" = "yes" ]
}

@test "loader.zsh warns when env.sh has not been deployed" {
  require_zsh || return 1
  mkdir -p "$FAKE_HOME/.config/zsh/config.d"
  cp "$REPO_ROOT/zsh/config.d/loader.zsh" "$FAKE_HOME/.config/zsh/config.d/loader.zsh"

  run --separate-stderr env -i HOME="$FAKE_HOME" PATH=/usr/bin:/bin zsh -f -c \
    'source "$HOME/.config/zsh/config.d/loader.zsh"'

  [ "$status" -eq 0 ]
  [[ "$stderr" == *"env.sh is missing"*"~/.local/bin/otto-workbench sync"* ]]
}

# ── one definition: constants and the herdr preamble agree with env.sh ────────

@test "env.sh lists the tool directories lib/constants.sh names, in ensure order" {
  # The caller's PATH, so `bash` is the modern one lib/roots.sh requires.
  run env -i HOME="$FAKE_HOME" PATH="$PATH" bash -c '
    . "$1/lib/constants.sh"
    printf "%s\n" "$LOCAL_BIN_DIR:$PI_BIN_DIR:$MISE_SHIMS_DIR"
    grep -qF "$HOMEBREW_BIN_DIR_MACOS" "$1/zsh/env.sh" && echo macos-brew-listed
    grep -qF "$HOMEBREW_BIN_DIR_LINUX" "$1/zsh/env.sh" && echo linux-brew-listed
  ' _ "$REPO_ROOT"
  local from_constants="${lines[0]}"

  [ "$status" -eq 0 ]
  [ "${lines[1]}" = "macos-brew-listed" ]
  [ "${lines[2]}" = "linux-brew-listed" ]
  run _in_sh /usr/bin:/bin 'workbench_path; printf "%s" "$PATH"'
  [[ "$output" == "$from_constants:"* ]]
}

@test "ZSH_ENV_DST is the path herdr-app's remote preamble sources" {
  local dst rel
  dst="$(env -i HOME="$FAKE_HOME" PATH="$PATH" bash -c '. "$1/lib/constants.sh"; printf %s "$ZSH_ENV_DST"' _ "$REPO_ROOT")"
  rel="$(node --input-type=module -e "
    import { REMOTE_ENV_FILE } from '$REPO_ROOT/ai/herdr/app/mods/_lib/board_data.js';
    process.stdout.write(REMOTE_ENV_FILE);
  ")"

  [ "$dst" = "$FAKE_HOME/$rel" ]
}

@test "step_zsh_env deploys env.sh as a real file and is idempotent" {
  run env -i HOME="$FAKE_HOME" PATH="$PATH" NO_COLOR=1 bash -c '
    . "$1/lib/ui.sh"; . "$1/zsh/steps.sh"
    step_zsh_env >/dev/null
    WORKBENCH_SYNC=true step_zsh_env
  ' _ "$REPO_ROOT"

  [ "$status" -eq 0 ]
  [ -z "$output" ]
  [ -f "$FAKE_HOME/.config/zsh/env.sh" ]
  [ ! -L "$FAKE_HOME/.config/zsh/env.sh" ]
  cmp -s "$REPO_ROOT/zsh/env.sh" "$FAKE_HOME/.config/zsh/env.sh"
}
