#!/usr/bin/env bash
# Migration: swap the live superpowers package entry for the template's filtered
# one, so upstream's bootstrap extension stops loading.
#
# That extension splices the bootstrap into the transcript for the first agent
# run only, which invalidates every signed thinking block on the requests after
# it — see ai/pi/extensions/superpowers-bootstrap/bootstrap.ts. The template now
# declares the package with `"extensions": []`, and the workbench extension
# delivers the bootstrap in the system prompt instead.
#
# sync-settings.jq cannot make this change itself: it identifies entries by
# source minus the @ref, so the plain string an earlier sync wrote matches the
# new object entry and is kept as the operator's. Only a plain-string entry is
# replaced, because that is the shape the workbench wrote. An object entry
# already carries filters someone chose, and is left alone with a warning.

migration_20261001_superpowers_filter_extension() {
  [[ -f "$PI_SETTINGS_FILE" ]] || return "$MIGRATION_NOOP"

  local ident='git:github.com/obra/superpowers'
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
    warn "Pi's superpowers entry in $PI_SETTINGS_FILE carries its own filters — add \"extensions\": [] to it by hand"
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
  success "Pi's superpowers package now loads its skills without its bootstrap extension"
}
