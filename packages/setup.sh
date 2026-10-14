#!/usr/bin/env bash
# Interactive package installer.
# brew bundle on macOS with Homebrew; elsewhere each entry installs through
# install_package — Homebrew where it exists, else mise (bootstrapped if needed).
# Called by otto-workbench install; can also be run standalone: ./packages/setup.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
WORKBENCH_DIR="$(git -C "$SCRIPT_DIR" rev-parse --show-toplevel)"
. "$WORKBENCH_DIR/lib/ui.sh"

_PACKAGES_STATE_KEY="packages.stacks"

if packages_use_bundle; then
  # shellcheck source=packages/brew_backend.sh
  . "$SCRIPT_DIR/brew_backend.sh"
elif command -v brew >/dev/null 2>&1 || command -v mise >/dev/null 2>&1 || _bootstrap_mise; then
  # shellcheck source=packages/entry_backend.sh
  . "$SCRIPT_DIR/entry_backend.sh"
else
  warn "Neither Homebrew nor mise is available, and mise could not be installed"
  exit 0
fi
pkg_backend_init || exit 1

# _pkg_url TYPE NAME
# Generates a Homebrew URL. Tap packages link to the tap on GitHub.
_pkg_url() {
  local type="$1" name="$2"
  if [[ "$name" == *"/"* ]]; then
    echo "https://github.com/$(echo "$name" | cut -d'/' -f1)/$(echo "$name" | cut -d'/' -f2)"
  elif [[ "$type" == "cask" ]]; then
    echo "https://formulae.brew.sh/cask/$name"
  else
    echo "https://formulae.brew.sh/formula/$name"
  fi
}

# _pkg_show_packages FILE [--numbered]
# Prints each package with install status (✓/+/–) and URL.
# With --numbered, prepends a bracketed index for use in selection menus.
_pkg_show_packages() {
  local file="$1" numbered=false
  [[ "${2:-}" == "--numbered" ]] && numbered=true
  local index=1
  while IFS= read -r line; do
    [[ "$line" =~ ^(brew|cask)[[:space:]]+\"([^\"]+)\" ]] || continue
    local type="${BASH_REMATCH[1]}" full_name="${BASH_REMATCH[2]}"
    local short_name="${full_name##*/}"
    local prefix="" url mark extra
    url=$(_pkg_url "$type" "$full_name")
    [[ "$numbered" == true ]] && prefix="$(printf '[%d] ' "$index")"
    if pkg_is_installed "$type" "$full_name"; then
      echo -e "  ${DIM}✓ ${prefix}$(printf '%-28s' "$short_name") $url${NC}"
      index=$(( index + 1 ))
      continue
    fi
    mark="${GREEN}+${NC}"
    extra=""
    if extra=$(pkg_skip_reason "$type" "$full_name"); then
      mark="${DIM}–${NC}"
    fi
    echo -e "  ${mark} ${prefix}$(printf '%-28s' "$short_name") ${DIM}$url${extra:+ $extra}${NC}"
    index=$(( index + 1 ))
  done < "$file"
}

# _pkg_install_file FILE LABEL
# Shows packages with status + URL. Skips the prompt if all are already installed.
_pkg_install_file() {
  local file="$1" label="$2"
  echo
  info "$label:"
  _pkg_show_packages "$file"
  echo
  local any_installable=0 type full_name
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^(brew|cask)[[:space:]]+\"([^\"]+)\" ]] || continue
    type="${BASH_REMATCH[1]}" full_name="${BASH_REMATCH[2]}"
    if pkg_skip_reason "$type" "$full_name" >/dev/null; then
      continue
    fi
    any_installable=1
    break
  done < "$file"
  if [[ "$any_installable" -eq 0 ]]; then
    echo -e "  ${DIM}Nothing installable — skipping${NC}"
    return
  fi
  if pkg_all_installed "$file"; then
    echo -e "  ${DIM}All packages already installed — skipping${NC}"
    return
  fi
  if confirm "  Install $label?"; then
    if pkg_install_file "$file" "$label"; then
      success "$label installed"
    else
      warn "$label install failed"
    fi
  fi
}

# _pkg_pkgs FILE — prints comma-separated short package names from a Brewfile
_pkg_pkgs() {
  grep -E '^(brew|cask) ' "$1" \
    | grep -oE '"[^"]+"' | tr -d '"' | awk -F'/' '{print $NF}' \
    | paste -sd ',' - | sed 's/,/, /g'
}

# _pkg_select_packages FILE LABEL
# Shows each package numbered with status + URL. Lets user pick a subset, all, or skip.
# Subset installs packages individually; all uses pkg_install_file for the stack.
_pkg_select_packages() {
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
  _pkg_show_packages "$file" --numbered
  echo

  local sel
  select_menu sel "${#pkg_full[@]}" --default all
  [[ -z "$sel" ]] && return

  # All indices selected — use pkg_install_file for the stack
  local sel_count
  sel_count=$(wc -w <<< "$sel")
  if [[ "$sel_count" -eq "${#pkg_full[@]}" ]]; then
    if pkg_install_file "$file" "$label"; then
      success "$label installed"
    else
      warn "$label install failed"
    fi
    return
  fi

  # Subset — install individually. The backend reports a successful one-package
  # install (brew prints here via pkg_install_one; mise already did inside the
  # installer helper). Keep going after a failure so the rest of the subset still
  # runs.
  local num
  for num in $sel; do
    local idx=$(( num - 1 ))
    local type="${pkg_types[$idx]}" full="${pkg_full[$idx]}" short="${pkg_short[$idx]}"
    if pkg_is_installed "$type" "$full"; then
      echo -e "  ${DIM}✓ $short already installed${NC}"
    else
      pkg_install_one "$type" "$full" || true
    fi
  done
}

# _pkg_select_category CATEGORY_DIR CATEGORY_LABEL
# Single-stack categories go directly to package selection.
# Multi-stack categories show a stack menu first.
_pkg_select_category() {
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
  local i
  for i in "${!stack_names[@]}"; do
    local pkgs
    pkgs=$(_pkg_pkgs "${stack_files[$i]}")
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

# _pkg_select_optional PACKAGES_DIR
# Discovers category subdirs under PACKAGES_DIR, presents them grouped.
# User can select entire categories or drill into individual stacks.
# Only globs */ so a top-level file such as components.Brewfile is not a stack.
_pkg_select_optional() {
  local packages_dir="$1"
  local category_dirs=() category_labels=()
  local _rel

  for d in "$packages_dir"/*/; do
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
      description=$(_pkg_pkgs "${brewfiles[0]}")
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
    _pkg_select_category "${category_dirs[$((num - 1))]}" "${category_labels[$((num - 1))]}"
  done

  # Record selected stacks in install.yml — one write replacing the saved list,
  # so a declined menu above leaves the previous stacks recorded rather than
  # having emptied them on the way in.
  local _stacks=()
  for i in "${!_SELECTED_FILES[@]}"; do
    _rel="${_SELECTED_FILES[$i]#"$packages_dir/"}"
    _stacks+=("${_rel%.Brewfile}")
  done
  state_set_list "$_PACKAGES_STATE_KEY" "${_stacks[@]}"

  for i in "${!_SELECTED_FILES[@]}"; do
    _pkg_select_packages "${_SELECTED_FILES[$i]}" "${_SELECTED_LABELS[$i]}"
  done
}

_pkg_replay_saved() {
  local packages_dir="$1" stacks="$2"
  info "Using saved package stacks"
  local _stack _brewfile
  while IFS= read -r _stack; do
    _brewfile="$packages_dir/${_stack}.Brewfile"
    if [[ -f "$_brewfile" ]]; then
      _pkg_install_file "$_brewfile" "$_stack"
    else
      warn "Saved stack not found: $_stack"
    fi
  done <<< "$stacks"
}

_pkg_install_file "$CORE_MANIFEST" "core packages"

# Replay saved stacks or run interactive selection
_saved_stacks=$(state_get_list "$_PACKAGES_STATE_KEY")
if [[ -n "$_saved_stacks" ]] && [[ "${WORKBENCH_INTERACTIVE:-}" != "1" ]]; then
  _pkg_replay_saved "$PACKAGES_DIR" "$_saved_stacks"
else
  _pkg_select_optional "$PACKAGES_DIR"
fi

pkg_backend_finish

# shellcheck source=packages/summary.sh
. "$SCRIPT_DIR/summary.sh"
print_packages_summary
