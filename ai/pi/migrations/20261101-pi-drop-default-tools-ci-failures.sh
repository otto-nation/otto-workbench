#!/usr/bin/env bash
# Migration: lift the subtractive defaultTools drop an earlier sync wrote.
#
# The template used to set `"defaultTools": ["-gh_ci_failures"]` so interactive
# Pi would not load the unbounded gh_ci_failures tool. The github-pr package now
# bounds that tool; the template omits the key so the built-in stays on.
# sync-settings.jq object-merges live keys the template no longer names, so a
# machine that already synced keeps the drop until this drain removes it.
#
# Only the exact workbench-written value is deleted. Any other defaultTools
# array is the operator's and is left alone.

migration_20261101_pi_drop_default_tools_ci_failures() {
  [[ -f "$PI_SETTINGS_FILE" ]] || return "$MIGRATION_NOOP"

  jq -e '.defaultTools == ["-gh_ci_failures"]' "$PI_SETTINGS_FILE" > /dev/null || return "$MIGRATION_NOOP"

  local tmp="$PI_SETTINGS_FILE.tmp.$$"
  if ! jq 'del(.defaultTools)' "$PI_SETTINGS_FILE" > "$tmp"; then
    rm -f "$tmp"
    warn "Could not lift defaultTools from $PI_SETTINGS_FILE; left unchanged"
    return 1
  fi
  mv "$tmp" "$PI_SETTINGS_FILE"
  success "Pi no longer subtracts gh_ci_failures from defaultTools"
}
