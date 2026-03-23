#!/bin/bash
# Bootstrap script for workbench dotfiles.
#
# Usage: bash install.sh [--all]
#
# What it does (core — always runs):
#   1. Installs the `task` runner if not present
#   2. Symlinks all bin/ scripts to ~/.local/bin/
#   3. Deploys zsh snippets to ~/.config/zsh/config.d/{framework,tools,aliases,prompt}/
#   4. Sets up ~/.gitconfig includes and global git hooks (via git/setup.sh)
#   5. Symlinks Taskfile.yml and lib/ to ~/.config/task/
#   6. Adds ~/.local/bin to PATH in your shell rc file if needed
#
# Then presents a menu of optional components (brew, docker, iterm, ai).
# Each component is defined by a setup.sh + setup.conf in its directory.
# Components are run in the order listed in install.components.
#
# Flags:
#   --all   Skip the component selection menu and run all eligible components.
#
# Re-running is safe — existing symlinks are updated silently; real files prompt before overwrite.

set -e

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DOTFILES_DIR
. "$DOTFILES_DIR/lib/ui.sh"

# Auto-source all core steps.sh files (bin, git, task, zsh and any future additions).
# shellcheck source=/dev/null
for _f in "$WORKBENCH_DIR"/*/steps.sh; do
  [[ -f "$_f" ]] && . "$_f"
done
unset _f

# ─── Flags ────────────────────────────────────────────────────────────────────

INSTALL_ALL=false
for _arg in "$@"; do [[ "$_arg" == "--all" ]] && INSTALL_ALL=true; done
unset _arg

# ─── Core helpers ─────────────────────────────────────────────────────────────

# update_path_in_shell_rc — appends ~/.local/bin to PATH in the user's shell rc file
# (~/.zshrc or ~/.bashrc) if the entry is not already present. No-op on unsupported shells.
update_path_in_shell_rc() {
  local shell_rc="" shell_name
  shell_name=$(basename "$SHELL")
  case "$shell_name" in
    zsh)  shell_rc="$ZSHRC_FILE" ;;
    bash) shell_rc="$BASHRC_FILE" ;;
    *)    return ;;
  esac
  # shellcheck disable=SC2016  # single quotes intentional — literal $HOME written to rc file
  if grep -q 'export PATH="$HOME/.local/bin:$PATH"' "$shell_rc" 2>/dev/null; then return; fi

  echo; info "Adding $LOCAL_BIN_DIR to PATH in $shell_rc"
  # shellcheck disable=SC2016  # single quotes intentional — literal $HOME written to rc file
  { echo ''; echo '# Add local bin to PATH'; echo 'export PATH="$HOME/.local/bin:$PATH"'; } >> "$shell_rc"
  echo -e "  ${GREEN}✓${NC} Updated $shell_rc"
}

# ─── Component system ─────────────────────────────────────────────────────────

# conf_get FILE KEY — reads a key = value line from FILE.
# Returns the trimmed value or empty string if the key is not found.
conf_get() {
  local file=$1 key=$2
  grep -m1 "^${key}[[:space:]]*=" "$file" 2>/dev/null \
    | sed "s/^${key}[[:space:]]*=[[:space:]]*//"
}

# platform_supported PLATFORMS — returns 0 if the current OS matches PLATFORMS.
# PLATFORMS is a space-separated list of: macos, linux. Empty or "all" means always supported.
platform_supported() {
  local platforms="${1:-all}"
  [[ "$platforms" == "all" || -z "$platforms" ]] && return 0
  if [[ "$OSTYPE" == "darwin"* ]]; then
    [[ "$platforms" == *"macos"* ]]
  else
    [[ "$platforms" == *"linux"* ]]
  fi
}

COMPONENT_DIRS=()
COMPONENT_LABELS=()
COMPONENT_DESCS=()
COMPONENT_PLATFORMS=()

# validate_components REGISTRY — cross-checks the registry against the filesystem.
# Exits with a hard error if:
#   - a registered component directory does not exist
#   - a registered component is missing setup.conf
#   - a directory has setup.conf but is not registered (orphan)
validate_components() {
  local registry=$1 errors=0 component dir conf

  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    if [[ ! -d "$WORKBENCH_DIR/$component" ]]; then
      err "install.components: '$component' — directory not found"
      errors=$(( errors + 1 ))
    elif [[ ! -f "$WORKBENCH_DIR/$component/setup.conf" ]]; then
      err "install.components: '$component' — missing setup.conf"
      errors=$(( errors + 1 ))
    fi
  done < "$registry"

  for conf in "$WORKBENCH_DIR"/*/setup.conf; do
    [[ -f "$conf" ]] || continue
    dir=$(basename "$(dirname "$conf")")
    if ! grep -qx "$dir" "$registry" 2>/dev/null; then
      err "install.components: '$dir' has setup.conf but is not registered (orphan)"
      errors=$(( errors + 1 ))
    fi
  done

  if (( errors > 0 )); then
    err "$errors validation error(s) — fix install.components before continuing"
    exit 1
  fi
}

# discover_components REGISTRY — reads component metadata in registry order.
# Populates COMPONENT_DIRS, COMPONENT_LABELS, COMPONENT_DESCS, COMPONENT_PLATFORMS.
discover_components() {
  local registry=$1 component conf

  while IFS= read -r component; do
    [[ -z "$component" || "$component" =~ ^# ]] && continue
    conf="$WORKBENCH_DIR/$component/setup.conf"
    COMPONENT_DIRS+=("$component")
    COMPONENT_LABELS+=("$(conf_get "$conf" label)")
    COMPONENT_DESCS+=("$(conf_get "$conf" description)")
    COMPONENT_PLATFORMS+=("$(conf_get "$conf" platforms)")
  done < "$registry"
}

SELECTED_COMPONENTS=()

# select_components — presents a numbered menu and populates SELECTED_COMPONENTS.
# Platform-incompatible components are silently skipped.
# With --all flag or empty selection, all eligible components are selected.
select_components() {
  local eligible_dirs=() eligible_labels=() eligible_descs=()

  for i in "${!COMPONENT_DIRS[@]}"; do
    platform_supported "${COMPONENT_PLATFORMS[$i]}" || continue
    eligible_dirs+=("${COMPONENT_DIRS[$i]}")
    eligible_labels+=("${COMPONENT_LABELS[$i]}")
    eligible_descs+=("${COMPONENT_DESCS[$i]}")
  done

  if [[ ${#eligible_dirs[@]} -eq 0 ]]; then
    info "No components available for this platform"
    return
  fi

  if [[ "$INSTALL_ALL" == "true" ]]; then
    SELECTED_COMPONENTS=("${eligible_dirs[@]}")
    return
  fi

  echo
  info "Optional components:"
  for i in "${!eligible_dirs[@]}"; do
    printf "  [%d] %-22s ${DIM}%s${NC}\n" "$(( i + 1 ))" "${eligible_labels[$i]}" "${eligible_descs[$i]}"
  done
  echo

  local _sel
  select_menu _sel "${#eligible_dirs[@]}" --default all
  [[ -z "$_sel" ]] && return

  local num
  for num in $_sel; do
    SELECTED_COMPONENTS+=("${eligible_dirs[$((num - 1))]}")
  done
}

# run_components — executes setup.sh for each selected component.
# If setup.conf defines a `check` command, runs it first; skips the component if it exits 0.
# DOTFILES_DIR is exported so check commands can reference it.
run_components() {
  [[ ${#SELECTED_COMPONENTS[@]} -eq 0 ]] && return

  local total=${#SELECTED_COMPONENTS[@]} index=1 component label check_cmd

  for component in "${SELECTED_COMPONENTS[@]}"; do
    label=$(conf_get "$WORKBENCH_DIR/$component/setup.conf" label)
    check_cmd=$(conf_get "$WORKBENCH_DIR/$component/setup.conf" check)
    echo -e "\n${DIM}[$index/$total]${NC} ${BOLD}${label:-$component}${NC}"

    # shellcheck disable=SC2086
    if [[ -n "$check_cmd" ]] && bash -c "$check_cmd" > /dev/null 2>&1; then
      success "already configured"
    else
      bash "$WORKBENCH_DIR/$component/setup.sh"
    fi

    index=$(( index + 1 ))
  done
}

# print_install_summary — prints a structured summary after all components run.
# Sources each selected component's summary.sh (if present) and calls its
# print_<component>_summary() function, following the same discovery pattern
# used by ai/setup.sh for per-tool summaries.
print_install_summary() {
  local shell_name readme
  shell_name=$(basename "$SHELL")
  readme="$WORKBENCH_DIR/README.md"

  echo
  echo -e "${BOLD}${GREEN}━━━ All done ━━━${NC}"
  echo

  echo -e "  ${CYAN}Installed${NC}"
  echo -e "  ${DIM}  • bin scripts      → $LOCAL_BIN_DIR/${NC}"
  echo -e "  ${DIM}  • zsh snippets     → $ZSH_CONFIG_DIR/{framework,tools,aliases,prompt}/${NC}"
  echo -e "  ${DIM}  • gitconfig        → $GITCONFIG_FILE (includes git/.gitconfig + ~/.gitconfig.local)${NC}"
  echo -e "  ${DIM}  • global Taskfile  → $TASK_CONFIG_DIR/${NC}"
  local component
  for component in "${SELECTED_COMPONENTS[@]}"; do
    local label
    label=$(conf_get "$WORKBENCH_DIR/$component/setup.conf" label)
    echo -e "  ${DIM}  • ${label:-$component}${NC}"
  done

  echo
  echo -e "  ${CYAN}Next steps${NC}"
  echo -e "  ${DIM}  1. Reload your shell:${NC}  ${BOLD}exec $shell_name${NC}"

  for component in "${SELECTED_COMPONENTS[@]}"; do
    local summary_file="$WORKBENCH_DIR/$component/summary.sh"
    # shellcheck source=/dev/null
    [[ -f "$summary_file" ]] && . "$summary_file"
    declare -f "print_${component}_summary" > /dev/null && "print_${component}_summary"
  done

  echo
  echo -e "  ${DIM}Day-to-day reference:  $readme${NC}"
  echo
}

# ─── Core installation ────────────────────────────────────────────────────────

echo -e "${BOLD}${BLUE}Installing dotfiles...${NC}"
echo -e "  ${DIM}✓${NC} installed  ${DIM}✓ up to date  ⊘ skipped  ⚠ attention needed${NC}"
echo

step_task_install
echo

sync_bin
sync_git
sync_zsh

echo; info "ZSH configuration (.zshrc)"
step_zshrc

sync_task

update_path_in_shell_rc

echo -e "\n${BOLD}${GREEN}✓ Dotfiles installed!${NC}"

# ─── Components ───────────────────────────────────────────────────────────────

REGISTRY="$WORKBENCH_DIR/install.components"
validate_components "$REGISTRY"
discover_components  "$REGISTRY"
select_components
run_components

print_install_summary
