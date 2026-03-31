#!/bin/bash
# Editors sync steps — re-applies config for each installed editor.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

_EDITORS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Source sub-component steps so sync_zed and sync_sublime are available.
# shellcheck source=editors/zed/steps.sh
. "$_EDITORS_DIR/zed/steps.sh"
# shellcheck source=editors/sublime/steps.sh
. "$_EDITORS_DIR/sublime/steps.sh"

# sync_editors — re-applies config for each installed editor.
# Called automatically by otto-workbench sync via the sync_<component> convention.
sync_editors() {
  if [[ "$OSTYPE" != "darwin"* ]]; then
    echo -e "  ${DIM}⊘ editor sync is macOS-only${NC}"
    return
  fi
  sync_zed
  sync_sublime
}

# ─── Standalone execution ──────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Editors sync${NC}\n"
  sync_editors
  echo
  success "Editors sync complete!"
fi
