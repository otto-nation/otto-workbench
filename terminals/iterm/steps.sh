#!/bin/bash
# iTerm2 sync steps — re-imports color themes non-interactively.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).
#
# Theme import uses `open` to hand .itermcolors files to iTerm2's file-type handler,
# which registers them as Color Presets without opening a new window. _theme_installed
# checks before importing, so re-running is always safe.
#
# Font configuration is a one-time manual step — it cannot be scripted via `open`.
# Run iterm/setup.sh (or install.sh → iterm component) for the font reminder.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  _D="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  . "$_D/../lib/ui.sh"
  unset _D
fi

# ─── Helpers ──────────────────────────────────────────────────────────────────

_ITERM_BUNDLE_ID="com.googlecode.iterm2"
_ITERM_PRESETS_KEY="Custom Color Presets"

# _theme_installed NAME — returns 0 if NAME already exists in iTerm2's Color Presets plist.
_theme_installed() {
  local name="$1"
  defaults read "$_ITERM_BUNDLE_ID" "$_ITERM_PRESETS_KEY" 2>/dev/null | grep -q "\"$name\""
}

# _import_theme FILE — registers a .itermcolors file as an iTerm2 color preset.
# No-op if the theme is already installed.
_import_theme() {
  local file="$1"
  local name
  name=$(basename "$file" .itermcolors)
  if _theme_installed "$name"; then
    echo -e "  ${DIM}✓ $name (already installed)${NC}"
    return
  fi
  if open "$file" 2>/dev/null; then
    success "Imported color preset: $name"
  else
    warn "Could not import $name — is iTerm2 installed?"
  fi
}

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_iterm_themes — imports all .itermcolors files from ITERM_THEMES_SRC_DIR.
# Safe to re-run: _theme_installed guards against duplicate imports.
step_iterm_themes() {
  local theme_file
  for theme_file in "$ITERM_THEMES_SRC_DIR"/*.itermcolors; do
    [[ -e "$theme_file" ]] || continue
    _import_theme "$theme_file"
  done
}

# sync_iterm — no-op during sync.
# Theme import is a one-time setup step handled by iterm/setup.sh.
# Nothing to reconcile during sync — iTerm themes live in its own plist.
sync_iterm() {
  :
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}iTerm2 sync${NC}\n"

  sync_iterm

  echo
  success "iTerm2 sync complete!"
fi
