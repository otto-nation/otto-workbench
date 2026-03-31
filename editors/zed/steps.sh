#!/bin/bash
# Zed sync steps.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../../lib/ui.sh"
  unset _D
fi

# step_zed_settings — merges workbench-managed keys into ~/.config/zed/settings.json.
# Preserves any keys not listed in the template's _workbench manifest.
# Strips JSONC line comments before parsing — Zed's default file uses // comments.
step_zed_settings() {
  local existing="{}" content
  if [[ -f "$ZED_SETTINGS_FILE" ]]; then
    content=$(grep -v '^\s*//' "$ZED_SETTINGS_FILE")
    [[ -n "$content" ]] && existing="$content"
  fi

  local result
  result=$(jq -n \
    --argjson t "$(cat "$ZED_SETTINGS_SRC")" \
    --argjson e "$existing" \
    -f "$ZED_SYNC_SETTINGS_JQ") \
    || { err "Failed to merge Zed settings"; return 1; }

  printf '%s\n' "$result" > "$ZED_SETTINGS_FILE"
  if [[ "$existing" == "{}" ]]; then
    success "settings.json written"
  else
    success "settings.json synced"
  fi
}

# sync_zed — re-applies Zed settings if Zed's config dir exists.
sync_zed() {
  if [[ ! -d "$ZED_CONFIG_DIR" ]]; then
    echo -e "  ${DIM}⊘ Zed config dir not found — skipping${NC}"
    return
  fi
  echo; info "Zed settings ($ZED_SETTINGS_FILE)"
  step_zed_settings
}

# ─── Standalone execution ──────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Zed sync${NC}\n"
  sync_zed
  echo
  success "Zed sync complete!"
fi
