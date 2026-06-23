#!/usr/bin/env bash
# description: Pi coding agent config
# Pi setup steps — sourced by ai/setup.sh.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

PI_HOME="$HOME/.pi"

# step_pi_settings — copies Pi settings.json to ~/.pi/.
# Creates ~/.pi/ if it doesn't exist.
step_pi_settings() {
  mkdir -p "$PI_HOME"
  install_file "$PI_SETTINGS_SRC" "$PI_HOME/settings.json" "Pi settings"
}

# _export_pi_config DIR — copies Pi config into DIR for tarball export.
_export_pi_config() {
  local dest="$1"
  mkdir -p "$dest"
  if [[ -f "$PI_SETTINGS_SRC" ]]; then
    cp "$PI_SETTINGS_SRC" "$dest/settings.json"
  fi
}

# sync_pi — runs all Pi sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
sync_pi() {
  sync_header "pi settings → $PI_HOME/"
  step_pi_settings
}

register_pi_steps() {
  register_step "Pi settings" step_pi_settings
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Pi sync${NC}\n"
  sync_pi
  echo
  success "Pi sync complete!"
fi
