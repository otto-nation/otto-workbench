#!/usr/bin/env bash
# Migration: install the Pi launcher the vendor installer manages, on a machine
# whose `pi` came from somewhere else.
#
# step_install_pi used to gate on `command -v pi`, which an npm-global copy
# under Homebrew's node answers exactly as readily as the installer's launcher.
# Such a machine skipped the install and reported success, and the copy it kept
# cannot be replaced by `pi update` — the reason the vendor installer is used
# at all. The setup step now tests PI_NATIVE_BIN by name, but setup steps do not
# run during sync, so an already-shadowed machine needs this.
#
# The shadow is reported, never removed. It may be something the operator
# installed deliberately, and which copy wins is a question about PATH order
# that belongs to them. Once ~/.local/bin precedes it, `pi` is the managed one.
#
# No adoption-sensitive header: ~/.local/bin and Homebrew's prefix are neither
# the config root nor the state root, so legacy-root adoption never re-seeds
# what this writes.
#
# Gated on the recorded ai.tools selection because migrations are not: every
# */migrations directory is globbed unconditionally and run before the component
# state gate, so this file executes on a machine that chose only claude. Every
# other migration on this branch edits files; this one downloads and runs a
# network payload, which would install an agent the operator declined — once,
# silently, and recorded as applied.

migration_20260902_replace_shadow_pi() {
  local tool selected=false
  while IFS= read -r tool; do
    if [[ "$tool" == "pi" ]]; then selected=true; fi
  done < <(state_get_list "ai.tools")

  # NOOP, not deferred: an operator who adds pi later gets the managed copy from
  # step_install_pi, which this migration exists only to reach behind.
  if [[ "$selected" != true ]]; then
    return "$MIGRATION_NOOP"
  fi

  # NOOP, not deferred: a machine with no pi has nothing shadowed, and one that
  # installs pi later gets the managed copy from step_install_pi.
  [[ -x "$PI_NATIVE_BIN" ]] && return "$MIGRATION_NOOP"

  local shadow
  shadow="$(command -v pi 2> /dev/null)" || return "$MIGRATION_NOOP"
  [[ -n "$shadow" ]] || return "$MIGRATION_NOOP"

  info "pi resolves to $shadow, not the installer's $PI_NATIVE_BIN — installing the managed copy"
  if ! run_remote_installer "$PI_INSTALL_URL"; then
    warn "Pi's installer failed — install it manually: $PI_INSTALL_URL"
    return 1
  fi

  if [[ ! -x "$PI_NATIVE_BIN" ]]; then
    warn "Pi's installer did not write $PI_NATIVE_BIN — install it manually: $PI_INSTALL_URL"
    return 1
  fi

  success "Pi installed at $PI_NATIVE_BIN; $shadow is still on PATH and was left alone"
}
