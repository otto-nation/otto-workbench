#!/usr/bin/env bash
# Sublime Text sync steps.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../../lib/ui.sh"
  unset _D
fi

# step_sublime_settings — merges workbench-managed keys into Preferences.sublime-settings.
# Preserves any keys not listed in the template's _workbench manifest.
step_sublime_settings() {
  mkdir -p "$SUBLIME_PREFS_DIR"

  local existing="{}" content
  if [[ -f "$SUBLIME_SETTINGS_FILE" ]]; then
    content=$(cat "$SUBLIME_SETTINGS_FILE")
    [[ -n "$content" ]] && existing="$content"
  fi

  local result
  result=$(jq -n \
    --argjson t "$(cat "$SUBLIME_SETTINGS_SRC")" \
    --argjson e "$existing" \
    -f "$SUBLIME_SYNC_SETTINGS_JQ") \
    || { err "Failed to merge Sublime settings"; return 1; }

  printf '%s\n' "$result" > "$SUBLIME_SETTINGS_FILE"
  if [[ "$existing" == "{}" ]]; then
    success "Preferences.sublime-settings written"
  else
    success "Preferences.sublime-settings synced"
  fi
}

# sync_sublime — re-applies Sublime settings if Packages/User dir exists.
sync_sublime() {
  if [[ ! -d "$SUBLIME_PREFS_DIR" ]]; then
    echo -e "  ${DIM}⊘ Sublime Text not installed — skipping${NC}"
    return
  fi
  echo; info "Sublime Text settings ($SUBLIME_SETTINGS_FILE)"
  step_sublime_settings
}

# ─── Standalone execution ──────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Sublime sync${NC}\n"
  sync_sublime
  echo
  success "Sublime sync complete!"
fi
