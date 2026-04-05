#!/usr/bin/env bash
# Interactive Homebrew package installer.
# Sourced by install.sh; can also be run standalone: bash brew/setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"

require_command brew "Homebrew not found — skipping package install" || exit 0
require_command jq  "jq not found — required for brew install status" || exit 1

# Build installed package sets once from brew's own metadata.
# Using brew info JSON (formula name / cask token) rather than keg names means
# aliases like delta→git-delta resolve correctly without per-package subprocesses.
_BREW_INFO=$(brew info --installed --json=v2 2>/dev/null)
if ! printf '%s' "$_BREW_INFO" | jq empty 2>/dev/null; then
  err "brew info returned invalid JSON — install status will not be shown correctly"
  _BREW_INFO='{}'
fi
_INSTALLED_FORMULAE=$(printf '%s' "$_BREW_INFO" | jq -r '.formulae[] | (.name, .aliases[])' 2>/dev/null)
_INSTALLED_CASKS=$(printf '%s' "$_BREW_INFO" | jq -r '.casks[].token' 2>/dev/null)
unset _BREW_INFO

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

# _brew_is_installed TYPE FULL_NAME — checks the cached brew info sets.
_brew_is_installed() {
  local type="$1" short_name="${2##*/}"
  if [[ "$type" == "cask" ]]; then
    echo "$_INSTALLED_CASKS"    | grep -qx "$short_name"
  else
    echo "$_INSTALLED_FORMULAE" | grep -qx "$short_name"
  fi
}

# _brew_all_installed FILE — returns 0 if every dependency in FILE is satisfied.
# Delegates to brew bundle check so brew's own resolver handles aliases and taps.
_brew_all_installed() {
  brew bundle check --file="$1" --no-upgrade &>/dev/null
}

# _brew_show_packages FILE [--numbered]
# Prints each package with install status (✓/+) and URL.
# With --numbered, prepends a bracketed index for use in selection menus.
_brew_show_packages() {
  local file="$1" numbered=false
  [[ "${2:-}" == "--numbered" ]] && numbered=true
  local index=1
  while IFS= read -r line; do
    [[ "$line" =~ ^(brew|cask)[[:space:]]+\"([^\"]+)\" ]] || continue
    local type="${BASH_REMATCH[1]}" full_name="${BASH_REMATCH[2]}"
    local short_name="${full_name##*/}"
    local prefix="" url
    url=$(_brew_pkg_url "$type" "$full_name")
    [[ "$numbered" == true ]] && prefix="$(printf '[%d] ' "$index")"
    if _brew_is_installed "$type" "$full_name"; then
      echo -e "  ${DIM}✓ ${prefix}$(printf '%-28s' "$short_name") $url${NC}"
    else
      echo -e "  ${GREEN}+${NC} ${prefix}$(printf '%-28s' "$short_name") ${DIM}$url${NC}"
    fi
    index=$(( index + 1 ))
  done < "$file"
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
  if confirm "  Install $label?"; then
    brew bundle --file="$file" && success "$label installed"
  fi
}

# _brew_pkgs FILE — prints comma-separated short package names from a Brewfile
_brew_pkgs() {
  grep -E '^(brew|cask) ' "$1" \
    | grep -oE '"[^"]+"' | tr -d '"' | awk -F'/' '{print $NF}' \
    | paste -sd ',' - | sed 's/,/, /g'
}

# _brew_select_packages FILE LABEL
# Shows each package numbered with status + URL. Lets user pick a subset, all, or skip.
# Subset installs packages individually; all uses brew bundle for full dependency handling.
_brew_select_packages() {
  local file="$1" label="$2"
  local pkg_types=() pkg_full=() pkg_short=()

  while IFS= read -r line; do
    [[ "$line" =~ ^(brew|cask)[[:space:]]+\"([^\"]+)\" ]] || continue
    pkg_types+=("${BASH_REMATCH[1]}")
    pkg_full+=("${BASH_REMATCH[2]}")
    pkg_short+=("${BASH_REMATCH[2]##*/}")
  done < "$file"

  [[ ${#pkg_full[@]} -eq 0 ]] && return

  echo
  info "$label:"
  _brew_show_packages "$file" --numbered
  echo

  local sel
  select_menu sel "${#pkg_full[@]}" --default all
  [[ -z "$sel" ]] && return

  # All indices selected — use brew bundle for proper dependency handling
  local sel_count
  sel_count=$(wc -w <<< "$sel")
  if [[ "$sel_count" -eq "${#pkg_full[@]}" ]]; then
    brew bundle --file="$file" && success "$label installed"
    return
  fi

  # Subset — install individually
  local num
  for num in $sel; do
    local idx=$(( num - 1 ))
    local type="${pkg_types[$idx]}" full="${pkg_full[$idx]}" short="${pkg_short[$idx]}"
    if _brew_is_installed "$type" "$full"; then
      echo -e "  ${DIM}✓ $short already installed${NC}"
    elif [[ "$type" == "cask" ]]; then
      brew install --cask "$full" && success "Installed $short"
    else
      brew install "$full" && success "Installed $short"
    fi
  done
}

# _brew_select_category CATEGORY_DIR CATEGORY_LABEL
# Single-stack categories go directly to package selection.
# Multi-stack categories show a stack menu first.
_brew_select_category() {
  local dir="$1" label="$2"
  local stack_files=() stack_names=()

  for f in "$dir"/*.Brewfile; do
    [[ -f "$f" ]] || continue
    stack_files+=("$f")
    stack_names+=("$(basename "$f" .Brewfile)")
  done

  [[ ${#stack_files[@]} -eq 0 ]] && return

  # Single stack — skip intermediate menu, go straight to package selection
  if [[ ${#stack_files[@]} -eq 1 ]]; then
    _SELECTED_FILES+=("${stack_files[0]}")
    _SELECTED_LABELS+=("$label")
    return
  fi

  # Multiple stacks — let user pick which ones
  echo
  info "$label:"
  for i in "${!stack_names[@]}"; do
    local pkgs
    pkgs=$(_brew_pkgs "${stack_files[$i]}")
    printf "  [%d] %-20s ${DIM}%s${NC}\n" "$((i+1))" "${stack_names[$i]}" "$pkgs"
  done
  echo

  local _sel
  select_menu _sel "${#stack_files[@]}" --default all
  [[ -z "$_sel" ]] && return

  local num
  for num in $_sel; do
    _SELECTED_FILES+=("${stack_files[$((num - 1))]}")
    _SELECTED_LABELS+=("${stack_names[$((num - 1))]} stack")
  done
}

# _brew_select_optional BREW_DIR
# Discovers category subdirs under BREW_DIR, presents them grouped.
# User can select entire categories or drill into individual stacks.
_brew_select_optional() {
  local brew_dir="$1"
  local category_dirs=() category_labels=()

  for d in "$brew_dir"/*/; do
    [[ -d "$d" ]] || continue
    local has_brewfile=0
    for f in "$d"*.Brewfile; do [[ -f "$f" ]] && has_brewfile=1 && break; done
    [[ "$has_brewfile" -eq 1 ]] || continue
    category_dirs+=("$d")
    category_labels+=("$(basename "$d")")
  done

  [[ ${#category_dirs[@]} -eq 0 ]] && return

  echo
  info "Optional stacks — select categories to install:"
  local i
  for i in "${!category_labels[@]}"; do
    local brewfiles=("${category_dirs[$i]}"*.Brewfile)
    local description
    if [[ ${#brewfiles[@]} -eq 1 ]] && [[ -f "${brewfiles[0]}" ]]; then
      # Single stack — show package names so the user knows what they're getting
      description=$(_brew_pkgs "${brewfiles[0]}")
    else
      # Multiple stacks — show stack names; packages are shown when drilling in
      description=$(for f in "${category_dirs[$i]}"*.Brewfile; do
        [[ -f "$f" ]] && basename "$f" .Brewfile
      done | paste -sd ',' - | sed 's/,/, /g')
    fi
    printf "  [%d] %-12s ${DIM}%s${NC}\n" "$((i+1))" "${category_labels[$i]}" "$description"
  done
  echo

  local _sel
  select_menu _sel "${#category_dirs[@]}" --default all
  [[ -z "$_sel" ]] && return

  local _SELECTED_FILES=() _SELECTED_LABELS=()
  local num
  for num in $_sel; do
    _brew_select_category "${category_dirs[$((num - 1))]}" "${category_labels[$((num - 1))]}"
  done

  for i in "${!_SELECTED_FILES[@]}"; do
    _brew_select_packages "${_SELECTED_FILES[$i]}" "${_SELECTED_LABELS[$i]}"
  done
}

# _brew_migrate_version_managers
# Detects nvm and jenv installed via Homebrew (now replaced by mise) and prompts
# the user to uninstall them. Also offers to remove leftover home directories.
# Only called during interactive install — not during otto-workbench sync.
_brew_migrate_version_managers() {
  local found=0

  if _brew_is_installed formula nvm; then
    found=1
    echo
    warn "nvm is installed — mise (now in core packages) replaces it."
    if confirm "  Uninstall nvm via Homebrew?"; then
      brew uninstall nvm
      success "nvm uninstalled"
    fi
    if [[ -d "$HOME/.nvm" ]]; then
      echo -e "  ${DIM}  ~/.nvm still exists (your downloaded Node versions are there)${NC}"
      if confirm "  Remove ~/.nvm?"; then
        rm -rf "$HOME/.nvm"
        success "$HOME/.nvm removed"
      fi
    fi
  fi

  if _brew_is_installed formula jenv; then
    found=1
    echo
    warn "jenv is installed — mise replaces it."
    if confirm "  Uninstall jenv via Homebrew?"; then
      brew uninstall jenv
      success "jenv uninstalled"
    fi
    if [[ -d "$HOME/.jenv" ]]; then
      echo -e "  ${DIM}  ~/.jenv still exists (your shims and version config are there)${NC}"
      if confirm "  Remove ~/.jenv?"; then
        rm -rf "$HOME/.jenv"
        success "$HOME/.jenv removed"
      fi
    fi
  fi

  if [[ "$found" -eq 1 ]]; then echo -e "  ${DIM}Re-add runtimes with: mise use node@lts  |  mise use java@21${NC}"; fi
}

_brew_install_file "$SCRIPT_DIR/Brewfile" "core packages"
_brew_select_optional "$SCRIPT_DIR"
_brew_migrate_version_managers

# shellcheck source=brew/summary.sh
. "$SCRIPT_DIR/summary.sh"
print_brew_summary
