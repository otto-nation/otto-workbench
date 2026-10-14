#!/usr/bin/env bash
# Package backend for machines with Homebrew — brew bundle for whole stacks,
# brew install for a subset, then version-manager migration and autoupdate.
# Sourced by packages/setup.sh.

pkg_backend_init() {
  require_command jq "jq not found — required for brew install status" || return 1

  # Build installed package sets once from brew's own metadata.
  # Using brew info JSON (formula name / cask token) rather than keg names means
  # aliases like delta→git-delta resolve correctly without per-package subprocesses.
  local info
  info=$(brew info --installed --json=v2 2>/dev/null)
  if ! printf '%s' "$info" | jq empty 2>/dev/null; then
    err "brew info returned invalid JSON — install status will not be shown correctly"
    info='{}'
  fi
  _INSTALLED_FORMULAE=$(printf '%s' "$info" | jq -r '.formulae[] | (.name, .aliases[])' 2>/dev/null)
  _INSTALLED_CASKS=$(printf '%s' "$info" | jq -r '.casks[].token' 2>/dev/null)
}

# pkg_skip_reason TYPE FULL_NAME — 0 when this backend will not install it
# (status display shows – rather than +). Homebrew installs everything it lists.
pkg_skip_reason() {
  return 1
}

# pkg_is_installed TYPE FULL_NAME — checks the cached brew info sets.
pkg_is_installed() {
  local type="$1" short_name="${2##*/}"
  if [[ "$type" == "cask" ]]; then
    echo "$_INSTALLED_CASKS" | grep -qx "$short_name"
  else
    echo "$_INSTALLED_FORMULAE" | grep -qx "$short_name"
  fi
}

# pkg_all_installed FILE — 0 if every dependency in FILE is satisfied.
# Delegates to brew bundle check so brew's own resolver handles aliases and taps.
pkg_all_installed() {
  brew bundle check --file="$1" --no-upgrade &>/dev/null
}

# pkg_install_file FILE LABEL — installs the whole stack via brew bundle.
pkg_install_file() {
  brew bundle --file="$1"
}

# pkg_install_one TYPE FULL_NAME — installs a single formula or cask.
pkg_install_one() {
  local type="$1" full="$2" short="${2##*/}"
  if [[ "$type" == "cask" ]]; then
    brew install --cask "$full" || return 1
  else
    brew install "$full" || return 1
  fi
  success "Installed $short"
}

# _brew_remove_version_manager NAME HOME_DIR
# Offers to uninstall a brew formula and remove its home directory.
_brew_remove_version_manager() {
  local name="$1" home_dir="$2"
  echo
  warn "${name} is installed — mise replaces it."
  if confirm "  Uninstall ${name} via Homebrew?"; then
    brew uninstall "$name"
    success "${name} uninstalled"
  fi
  [[ ! -d "$home_dir" ]] && return
  echo -e "  ${DIM}  ${home_dir} still exists${NC}"
  if confirm "  Remove ${home_dir}?"; then
    rm -rf "$home_dir"
    success "${home_dir} removed"
  fi
}

# pkg_backend_finish — detects nvm and jenv installed via Homebrew (now replaced
# by mise) and prompts the user to uninstall them. Also offers to remove leftover
# home directories and to enable brew autoupdate.
# Only called during interactive install — not during otto-workbench sync.
pkg_backend_finish() {
  local found=0

  if pkg_is_installed formula nvm; then
    found=1
    _brew_remove_version_manager nvm "$HOME/.nvm"
  fi

  if pkg_is_installed formula jenv; then
    found=1
    _brew_remove_version_manager jenv "$HOME/.jenv"
  fi

  if [[ "$found" -eq 1 ]]; then
    echo -e "  ${DIM}Re-add runtimes with: mise use node@lts  |  mise use java@21${NC}"
  fi

  # brew autoupdate (DomT4/autoupdate tap) schedules brew update/upgrade via launchd.
  if brew autoupdate status 2>/dev/null | grep -q "not running"; then
    echo
    info "Homebrew autoupdate is not running."
    echo -e "  ${DIM}Upgrades all formulae and casks every 12 hours and on every system boot.${NC}"
    echo -e "  ${DIM}Casks with built-in auto-updates are skipped. Prompts for sudo when needed.${NC}"
    echo -e "  ${DIM}Old versions and left-over files are cleaned up automatically.${NC}"
    if confirm "  Enable brew autoupdate?"; then
      brew autoupdate start 43200 --upgrade --cleanup --immediate --sudo
      success "Homebrew autoupdate enabled"
    fi
  fi
}
