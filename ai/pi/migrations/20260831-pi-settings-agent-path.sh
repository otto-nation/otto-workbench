#!/usr/bin/env bash
# Migration: remove the Pi settings file an earlier sync wrote to a path Pi does
# not read.
#
# Pi's global settings live at ~/.pi/agent/settings.json. The sync wrote
# ~/.pi/settings.json, so every key in it — the default provider, model and
# thinking level — has been inert since it was first installed. step_pi_settings
# now writes the real path; this drains the file that never applied.
#
# The contents are deliberately not merged forward. Pi never read them, so any
# hand edit there is configuration the operator believes is inactive, and
# activating it silently is the wrong surprise. A file that still matches the
# template carried nothing to lose and is simply removed; one that has drifted is
# kept alongside so the operator can decide what, if anything, to re-apply.

migration_20260831_pi_settings_agent_path() {
  [[ -f "$PI_LEGACY_SETTINGS_FILE" ]] || return "$MIGRATION_NOOP"

  if diff -q "$PI_SETTINGS_SRC" "$PI_LEGACY_SETTINGS_FILE" > /dev/null 2>&1; then
    rm -f "$PI_LEGACY_SETTINGS_FILE"
    success "Removed the inert Pi settings at $PI_LEGACY_SETTINGS_FILE"
    return 0
  fi

  local kept="$PI_LEGACY_SETTINGS_FILE.pre-move"
  mv "$PI_LEGACY_SETTINGS_FILE" "$kept"
  warn "Pi never read $PI_LEGACY_SETTINGS_FILE — its edits are kept at $kept"
  success "Pi settings now sync to $PI_SETTINGS_FILE"
}
