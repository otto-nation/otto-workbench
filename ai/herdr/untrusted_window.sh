#!/usr/bin/env bash
# Untrusted herdr-app window — sourced by ai/herdr/steps.sh. Never starts hc:
# any hc command would start a core. Opt-in: step_herdr_untrusted_window is a
# no-op until HERDR_UNTRUSTED_APP_DIR exists (the launcher creates it).
#
# ceiling: the untrusted window runs as the same macOS user as the operator;
# a forged confirm dialog still executes as the operator (finding 2).
# Upgrade trigger: if either advisory (GHSA-g48m-jxrp-5x36, GHSA-v4vx-vr3x-w873)
# is closed as won't-fix or out of scope, or if the untrusted session is ever
# found holding a local terminal or an afar grant.

# Appearance keys copied from the main window. Derived from herdr-app v0.1.13
# Person in src/settings.rs: font (L377), font_size (L380), font_ligatures
# (L382), ui_font (L385), minimum_contrast (L395), appearance (L402), theme
# (L404), colors (L406). clipboard_write (L391) is forced false rather than
# copied. zoom, sounds, core, telemetry, pane_scrollbars and option_as_alt
# stay as the untrusted window wrote them.
_HERDR_UNTRUSTED_APPEARANCE_KEYS='["appearance","colors","font","font_ligatures","font_size","minimum_contrast","theme","ui_font"]'
_HERDR_UNTRUSTED_SHARED_SEED='{"frame.welcomed":"skipped","layout":"main"}'

# _herdr_untrusted_state_home — herdr-core's XDG_STATE_HOME: unset or empty
# falls back to ~/.local/state (herdr-core paths.rs:20-23).
_herdr_untrusted_state_home() {
  if [[ -n "${XDG_STATE_HOME:-}" ]]; then
    printf '%s\n' "$XDG_STATE_HOME"
    return 0
  fi
  printf '%s\n' "$HOME/.local/state"
  return 0
}

_herdr_untrusted_core_dir() {
  printf '%s\n' "$(_herdr_untrusted_state_home)/herdr-core/$1"
}

_herdr_untrusted_is_object() {
  jq -se 'length == 1 and (.[0] | type == "object")' <<< "$1" >/dev/null 2>&1
}

# _herdr_untrusted_reconcile_settings — appearance keys from main, clipboard_write
# forced false, every other key kept. Warn and leave when main is missing or not
# one JSON object, or when the untrusted file is a symlink: herdr-app's
# write_atomic follows symlinks (persist.rs:8-22), so a link would let the
# untrusted window write into main's file.
_herdr_untrusted_reconcile_settings() {
  local main_file="$HERDR_APP_SETTINGS_FILE"
  local file="$HERDR_UNTRUSTED_APP_DIR/settings.json"
  local main current desired
  if [[ -L "$file" ]]; then
    warn "$file is a symlink — left as is; herdr-app write_atomic would follow it into the main window's settings"
    return 0
  fi
  if [[ ! -e "$main_file" ]]; then
    warn "herdr-app-untrusted: $main_file is missing — left untrusted settings as is"
    return 0
  fi
  main=$(cat "$main_file") || { warn "Could not read $main_file"; return 0; }
  if ! _herdr_untrusted_is_object "$main"; then
    warn "$main_file is not a JSON object — left untrusted settings as is; fix it and re-run otto-workbench sync"
    return 0
  fi
  current='{}'
  if [[ -e "$file" ]]; then
    current=$(cat "$file") || { warn "Could not read $file"; return 0; }
    if ! _herdr_untrusted_is_object "$current"; then
      warn "$file is not a JSON object — left as is; fix it and re-run otto-workbench sync"
      return 0
    fi
  fi
  desired=$(jq -n --argjson main "$main" --argjson cur "$current" --argjson keys "$_HERDR_UNTRUSTED_APPEARANCE_KEYS" '
    reduce $keys[] as $k ($cur;
      if $main | has($k) then .[$k] = $main[$k] else del(.[$k]) end
    ) | .clipboard_write = false
  ') || { warn "Could not derive untrusted settings from $main_file"; return 0; }
  if jq -ne --argjson a "$current" --argjson b "$desired" '$a == $b' >/dev/null; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "herdr-app-untrusted settings follow the main window" || true
    return 0
  fi
  wb_write_json_file "$file" "$desired" \
    || { warn "Could not write $file — untrusted settings not updated"; return 0; }
  success "herdr-app-untrusted settings follow the main window (clipboard_write=false)"
  return 0
}

# _herdr_untrusted_link_keys — keys.json → symlink to main. keys::load_text only
# rewrites untouched shipped defaults (keys.rs:172-186) and plain fs::write
# follows the link. Identical regular file → replaced; differing → warned and left.
_herdr_untrusted_link_keys() {
  local src="$HERDR_APP_DIR/keys.json"
  local target="$HERDR_UNTRUSTED_APP_DIR/keys.json"
  if [[ -f "$target" && ! -L "$target" ]]; then
    if [[ -f "$src" ]] && ! cmp -s "$src" "$target"; then
      warn "herdr-app-untrusted keys.json: $target differs from $src — left as is; delete it and re-run otto-workbench sync to link it"
      return 0
    fi
    rm "$target"
  fi
  install_symlink "$src" "$target" "herdr-app-untrusted keys.json" --no-prompt
  return 0
}

_herdr_untrusted_seed_shared() {
  local file="$HERDR_UNTRUSTED_APP_DIR/shared.json"
  [[ -e "$file" ]] && return 0
  wb_write_json_file "$file" "$_HERDR_UNTRUSTED_SHARED_SEED" \
    || { warn "Could not write $file — welcome may create a local terminal"; return 0; }
  success "herdr-app-untrusted shared.json seeded (frame.welcomed=skipped)"
  return 0
}

_herdr_untrusted_reconcile() {
  mkdir -p "$HERDR_UNTRUSTED_APP_DIR"
  _herdr_untrusted_reconcile_settings
  _herdr_untrusted_link_keys
  _herdr_app_link_themes "$HERDR_UNTRUSTED_APP_DIR"
  _herdr_untrusted_seed_shared
  return 0
}

_herdr_untrusted_terminal_addr() {
  jq -r --arg k "$1" '.address // ("t" + $k)' <<< "$2"
}

# _herdr_untrusted_check_terminals — any terminal whose state is not finished,
# or any non-empty afar, is a violation. terminals.json is herdr-core Registry
# (terminals.rs:209-214): {next_id, holder, terminals: {id: Terminal}}.
# Terminal.state is "live" or "finished" (L35-36); afar is a set of machine ids
# (L140). Missing file: no terminals.
_herdr_untrusted_check_terminals() {
  local file core_dir raw entries line addr body failed=0
  core_dir=$(_herdr_untrusted_core_dir "$HERDR_UNTRUSTED_SESSION") || return 1
  file="$core_dir/terminals.json"
  [[ -e "$file" ]] || return 0
  raw=$(cat "$file") || { warn "herdr untrusted window: could not read $file"; return 1; }
  if ! _herdr_untrusted_is_object "$raw"; then
    warn "herdr untrusted window: $file is not a JSON object — cannot verify local terminals"
    return 1
  fi
  entries=$(jq -c '.terminals // {} | to_entries[]' <<< "$raw" 2>/dev/null) || {
    warn "herdr untrusted window: $file has no terminals object — cannot verify local terminals"
    return 1
  }
  [[ -z "$entries" ]] && return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "$line" ]] && continue
    addr=$(_herdr_untrusted_terminal_addr "$(jq -r '.key' <<< "$line")" "$(jq -c '.value' <<< "$line")")
    body=$(jq -c '.value' <<< "$line")
    if [[ "$(jq -r '.state // empty' <<< "$body")" != "finished" ]]; then
      warn "herdr untrusted window: $addr is not finished — run: HC_SESSION=$HERDR_UNTRUSTED_SESSION hc kill $addr"
      warn "  or: HC_SESSION=$HERDR_UNTRUSTED_SESSION hc rm $addr"
      failed=1
      continue
    fi
    if jq -e '((.afar // []) | length) > 0' <<< "$body" >/dev/null; then
      warn "herdr untrusted window: $addr has an afar grant — run: HC_SESSION=$HERDR_UNTRUSTED_SESSION hc rm $addr"
      failed=1
    fi
  done <<< "$entries"
  return "$failed"
}

# _herdr_untrusted_check_machine_overlap — a machine id in both
# untrusted/machines.json and default/machines.json. Each file is a JSON array
# of Machine (machines.rs:91-93, id at L29). Missing file: empty list.
_herdr_untrusted_machine_ids() {
  local file="$1"
  [[ -f "$file" ]] || { printf '%s\n' '[]'; return 0; }
  jq -c 'if type == "array" then [.[].id | select(type == "string" and . != "")] else error("not array") end' "$file"
}

_herdr_untrusted_check_machine_overlap() {
  local default_file untrusted_file default_ids untrusted_ids overlap id
  default_file="$(_herdr_untrusted_core_dir default)/machines.json"
  untrusted_file="$(_herdr_untrusted_core_dir "$HERDR_UNTRUSTED_SESSION")/machines.json"
  default_ids=$(_herdr_untrusted_machine_ids "$default_file") \
    || { warn "herdr untrusted window: $default_file is not a machine array — cannot verify overlap"; return 1; }
  untrusted_ids=$(_herdr_untrusted_machine_ids "$untrusted_file") \
    || { warn "herdr untrusted window: $untrusted_file is not a machine array — cannot verify overlap"; return 1; }
  overlap=$(jq -n --argjson d "$default_ids" --argjson u "$untrusted_ids" '$d - ($d - $u) | .[]') \
    || { warn "herdr untrusted window: could not compute machine overlap"; return 1; }
  [[ -z "$overlap" ]] && return 0
  while IFS= read -r id || [[ -n "$id" ]]; do
    [[ -z "$id" ]] && continue
    warn "herdr untrusted window: machine id $id is in both default and $HERDR_UNTRUSTED_SESSION"
  done <<< "$overlap"
  return 1
}

_herdr_untrusted_clipboard_off() {
  local file="$HERDR_UNTRUSTED_APP_DIR/settings.json"
  [[ -f "$file" && ! -L "$file" ]] || return 1
  jq -e '.clipboard_write == false' "$file" >/dev/null 2>&1
}

# _herdr_untrusted_ensure_clipboard — clipboard_write must be false. If it is
# not, re-reconcile (the app may have rewritten settings.json) and check again.
_herdr_untrusted_ensure_clipboard() {
  _herdr_untrusted_clipboard_off && return 0
  _herdr_untrusted_reconcile_settings
  _herdr_untrusted_clipboard_off && return 0
  warn "herdr untrusted window: clipboard_write is not false in $HERDR_UNTRUSTED_APP_DIR/settings.json"
  return 1
}

# _herdr_untrusted_preflight — reads state files only, never calls hc. Used by
# the sync step (warn) and the launcher (refuse).
_herdr_untrusted_preflight() {
  local failed=0
  _herdr_untrusted_check_terminals || failed=1
  _herdr_untrusted_check_machine_overlap || failed=1
  _herdr_untrusted_ensure_clipboard || failed=1
  return "$failed"
}

# step_herdr_untrusted_window — non-fatal. Quiet under WORKBENCH_SYNC when
# nothing changed. Never copies mods/, frame/, installed.json, data/,
# window.json, or view.json.
step_herdr_untrusted_window() {
  [[ -d "$HERDR_UNTRUSTED_APP_DIR" ]] || return 0
  _herdr_untrusted_reconcile
  if ! _herdr_untrusted_preflight; then
    warn "herdr untrusted window: local invariants failed — herdr-app-untrusted will refuse to open"
  fi
  return 0
}
