#!/usr/bin/env bash
# Migration: swap the live pi-extensions package entry for the template's
# filtered one, so its linear extension stops loading.
#
# Linear is reached through the `linear` CLI (see the issue-tracker rule). The
# extension's linear_* tools were a second path to the same tracker, one that
# fails outright on an auth-header bug, and the template now excludes it with
# `"extensions": ["!extensions/linear/**"]`.
#
# sync-settings.jq cannot make this change itself: it identifies entries by
# source minus the @ref, so the plain string an earlier sync wrote matches the
# new object entry and is kept as the operator's. Only a plain-string entry is
# replaced, because that is the shape the workbench wrote. An object entry
# already carries filters someone chose, and is left alone with a warning.

migration_20261020_pi_extensions_drop_linear() {
  [[ -f "$PI_SETTINGS_FILE" ]] || return "$MIGRATION_NOOP"

  local ident='git:github.com/usemaximum/pi-extensions'
  local wanted
  wanted=$(jq -c --arg id "$ident" '
    [(.packages // [])[] | select(type == "object" and (.source | sub("@[^@/:]+$"; "")) == $id)]
    | first // empty' "$PI_SETTINGS_SRC")
  [[ -n "$wanted" ]] || return "$MIGRATION_NOOP"

  local live
  live=$(jq -c --arg id "$ident" '
    [(.packages // [])[] | select((if type == "object" then .source else . end | sub("@[^@/:]+$"; "")) == $id)]
    | first // empty' "$PI_SETTINGS_FILE")
  [[ -n "$live" ]] || return "$MIGRATION_NOOP"

  if [[ "$(jq -r 'type' <<< "$live")" == "object" ]]; then
    [[ "$(jq -cS . <<< "$live")" == "$(jq -cS . <<< "$wanted")" ]] && return "$MIGRATION_NOOP"
    warn "Pi's pi-extensions entry in $PI_SETTINGS_FILE carries its own filters — add \"!extensions/linear/**\" to its \"extensions\" by hand"
    return "$MIGRATION_NOOP"
  fi

  # Sibling temp file and rename, so a failure mid-write leaves the original.
  local tmp="$PI_SETTINGS_FILE.tmp.$$"
  if ! jq --arg id "$ident" --argjson wanted "$wanted" '
      .packages |= map(if type == "string" and sub("@[^@/:]+$"; "") == $id then $wanted else . end)
    ' "$PI_SETTINGS_FILE" > "$tmp"; then
    rm -f "$tmp"
    return 1
  fi
  mv "$tmp" "$PI_SETTINGS_FILE"
  success "Pi's pi-extensions package now loads without its linear extension"
}
