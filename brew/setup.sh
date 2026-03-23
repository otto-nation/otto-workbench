#!/bin/bash
# Interactive Homebrew package installer.
# Sourced by install.sh; can also be run standalone: bash brew/setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"

require_command brew "Homebrew not found — skipping package install" || exit 0

# Cache installed formulae and casks once for fast per-package lookups
_INSTALLED_FORMULAE=$(brew list --formula 2>/dev/null)
_INSTALLED_CASKS=$(brew list --cask 2>/dev/null)

# _brew_pkg_url TYPE NAME
# Generates a Homebrew URL. Tap packages link to the tap on GitHub.
_brew_pkg_url() {
  local type="$1" name="$2"
  if [[ "$name" == *"/"* ]]; then
    echo "https://github.com/$(echo "$name" | cut -d'/' -f1)/$(echo "$name" | cut -d'/' -f2)"
  elif [[ "$type" == "cask" ]]; then
    echo "https://formulae.brew.sh/cask/$name"
  else
    echo "https://formulae.brew.sh/formula/$name"
  fi
}

# _brew_is_installed TYPE SHORT_NAME
_brew_is_installed() {
  local type="$1" name="$2"
  if [[ "$type" == "cask" ]]; then
    echo "$_INSTALLED_CASKS" | grep -qx "$name"
  else
    echo "$_INSTALLED_FORMULAE" | grep -qx "$name"
  fi
}

# _brew_show_packages FILE
# Prints each package with install status (✓/+) and URL.
_brew_show_packages() {
  local file="$1"
  while IFS= read -r line; do
    [[ "$line" =~ ^(brew|cask)[[:space:]]+\"([^\"]+)\" ]] || continue
    local type="${BASH_REMATCH[1]}" full_name="${BASH_REMATCH[2]}"
    local short_name="${full_name##*/}"
    local url
    url=$(_brew_pkg_url "$type" "$full_name")
    if _brew_is_installed "$type" "$short_name"; then
      echo -e "  ${DIM}✓ $(printf '%-28s' "$short_name") $url${NC}"
    else
      echo -e "  ${GREEN}+${NC} $(printf '%-28s' "$short_name") ${DIM}$url${NC}"
    fi
  done < "$file"
}

# _brew_all_installed FILE — returns 0 if every package in FILE is already installed.
_brew_all_installed() {
  local file="$1"
  while IFS= read -r line; do
    [[ "$line" =~ ^(brew|cask)[[:space:]]+\"([^\"]+)\" ]] || continue
    local type="${BASH_REMATCH[1]}" full_name="${BASH_REMATCH[2]}"
    _brew_is_installed "$type" "${full_name##*/}" || return 1
  done < "$file"
  return 0
}

# _brew_install_file FILE LABEL
# Shows packages with status + URL. Skips the prompt if all are already installed.
_brew_install_file() {
  local file="$1" label="$2"
  echo
  info "$label:"
  _brew_show_packages "$file"
  echo
  if _brew_all_installed "$file"; then
    echo -e "  ${DIM}All packages already installed — skipping${NC}"
    return
  fi
  confirm "  Install $label?" && brew bundle --file="$file" && success "$label installed"
}

# _brew_select_work_stacks WORK_DIR
# Shows available stacks with their packages, lets user pick by number.
_brew_select_work_stacks() {
  local work_dir="$1"
  local stack_files=() stack_names=()

  for f in "$work_dir"/*.Brewfile; do
    [[ -f "$f" ]] || continue
    stack_files+=("$f")
    stack_names+=("$(basename "$f" .Brewfile)")
  done

  [[ ${#stack_files[@]} -eq 0 ]] && return

  echo; info "Work stacks (brew/work/):"
  for i in "${!stack_names[@]}"; do
    local pkgs
    pkgs=$(grep -E '^(brew|cask) ' "${stack_files[$i]}" \
      | grep -oE '"[^"]+"' | tr -d '"' | awk -F'/' '{print $NF}' | paste -sd ',' - | sed 's/,/, /g')
    printf "  [%d] %-15s ${DIM}%s${NC}\n" "$((i+1))" "${stack_names[$i]}" "$pkgs"
  done
  echo

  local _sel
  select_menu _sel "${#stack_files[@]}" --default all
  [[ -z "$_sel" ]] && return

  local num
  for num in $_sel; do
    _brew_install_file "${stack_files[$((num - 1))]}" "${stack_names[$((num - 1))]} stack"
  done
}

_brew_install_file "$SCRIPT_DIR/Brewfile" "core packages"
_brew_select_work_stacks "$SCRIPT_DIR/work"
