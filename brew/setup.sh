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
    if _brew_is_installed "$type" "$short_name"; then
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
  local all_installed=true
  while IFS= read -r line; do
    [[ "$line" =~ ^(brew|cask)[[:space:]]+\"([^\"]+)\" ]] || continue
    _brew_is_installed "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]##*/}" || { all_installed=false; break; }
  done < "$file"
  if [[ "$all_installed" == true ]]; then
    echo -e "  ${DIM}All packages already installed — skipping${NC}"
    return
  fi
  confirm "  Install $label?" && brew bundle --file="$file" && success "$label installed"
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
    if _brew_is_installed "$type" "$short"; then
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

# _brew_resolve_category_input RAW_INPUT DIRS_ARRAY LABELS_ARRAY
# Resolves user input (numbers, names, or "all") against the parallel
# DIRS_ARRAY / LABELS_ARRAY and calls _brew_select_category for each match.
# Populates the caller's _SELECTED_FILES and _SELECTED_LABELS arrays.
_brew_resolve_category_input() {
  local raw_input="$1"
  local -n _dirs_ref="$2"
  local -n _labels_ref="$3"

  # Expand "all" to every category index
  if [[ "$raw_input" == "all" ]]; then
    raw_input=""
    local i
    for i in "${!_dirs_ref[@]}"; do raw_input+="$((i+1)) "; done
  fi

  # Resolve each token: number → index; name → matching label
  local token
  for token in $raw_input; do
    if [[ "$token" =~ ^[0-9]+$ ]]; then
      local idx=$(( token - 1 ))
      if [[ "$idx" -ge 0 && "$idx" -lt "${#_dirs_ref[@]}" ]]; then
        _brew_select_category "${_dirs_ref[$idx]}" "${_labels_ref[$idx]}"
      fi
    else
      local i
      for i in "${!_labels_ref[@]}"; do
        if [[ "${_labels_ref[$i]}" == "$token" ]]; then
          _brew_select_category "${_dirs_ref[$i]}" "${_labels_ref[$i]}"
          break
        fi
      done
    fi
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
  for i in "${!category_labels[@]}"; do
    local stacks
    stacks=$(for f in "${category_dirs[$i]}"*.Brewfile; do
      [[ -f "$f" ]] && basename "$f" .Brewfile
    done | paste -sd ',' - | sed 's/,/, /g')
    printf "  [%d] %-12s ${DIM}%s${NC}\n" "$((i+1))" "${category_labels[$i]}" "$stacks"
  done
  echo
  echo -e "  ${DIM}Enter numbers (e.g. 1 3), category names (e.g. lang infra), or 'all'${NC}"
  echo

  local raw_input
  printf "  Selection [all]: "
  read -r raw_input
  raw_input="${raw_input:-all}"

  local _SELECTED_FILES=() _SELECTED_LABELS=()
  _brew_resolve_category_input "$raw_input" category_dirs category_labels

  for i in "${!_SELECTED_FILES[@]}"; do
    _brew_select_packages "${_SELECTED_FILES[$i]}" "${_SELECTED_LABELS[$i]}"
  done
}

_brew_install_file "$SCRIPT_DIR/Brewfile" "core packages"
_brew_select_optional "$SCRIPT_DIR"
