#!/bin/bash
# Bootstrap script for workbench dotfiles.
#
# Usage: bash install.sh [--all]
#
# What it does (core — always runs):
#   1. Installs the `task` runner if not present  [preflight — explicit]
#   2. Auto-discovers and calls sync_<name>() for every core component
#      (core = has steps.sh but no setup.conf; currently: bin, git, task, zsh)
#      Adding a new core component requires only creating steps.sh — no edits here.
#   3. Adds ~/.local/bin to PATH in your shell rc file if needed
#
# Then presents a menu of optional components (brew, docker, iterm, ai).
# Each component is defined by a setup.sh + setup.conf in its directory.
# Components are run in the order listed in install.components.
# Components that declare `depends` in setup.conf have those deps auto-included.
#
# Flags:
#   --all   Skip the component selection menu and run all eligible components.
#
# Re-running is safe — existing symlinks are updated silently; real files prompt before overwrite.

set -e

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export DOTFILES_DIR
. "$DOTFILES_DIR/lib/ui.sh"
# Export WORKBENCH_DIR so it is available in setup.conf check commands (bash -c context).
export WORKBENCH_DIR

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

# validate_components REGISTRY — lightweight fast-fail guard before any side effects run.
# Checks only that registered components exist on disk and that no setup.conf is orphaned.
# This is intentionally a subset of bin/validate-components, which runs the full contract
# check (sync functions, dep ordering, registry schema) in CI and pre-push.
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
# Components with `depends` in setup.conf have their deps auto-included and
# re-sorted into install.components order so deps always run before dependents.
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

  local desired=()
  if [[ "$INSTALL_ALL" == "true" ]]; then
    desired=("${eligible_dirs[@]}")
  else
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
      desired+=("${eligible_dirs[$((num - 1))]}")
    done
  fi

  # Expand desired set with declared deps (iterate until stable).
  local _changed=true _comp _deps _dep _found _d
  while [[ "$_changed" == true ]]; do
    _changed=false
    for _comp in "${desired[@]}"; do
      _deps=$(conf_get "$WORKBENCH_DIR/$_comp/setup.conf" depends)
      [[ -z "$_deps" ]] && continue
      for _dep in $_deps; do
        _found=false
        for _d in "${desired[@]}"; do [[ "$_d" == "$_dep" ]] && { _found=true; break; }; done
        if [[ "$_found" == false ]]; then
          info "Adding $_dep (required by $_comp)"
          desired+=("$_dep")
          _changed=true
        fi
      done
    done
  done
  unset _changed _comp _deps _dep _found _d

  # Re-sort by install.components order so deps always precede dependents.
  local _c _d2
  for _c in "${COMPONENT_DIRS[@]}"; do
    for _d2 in "${desired[@]}"; do
      [[ "$_c" == "$_d2" ]] && { SELECTED_COMPONENTS+=("$_c"); break; }
    done
  done
  unset _c _d2
}

# run_components — executes setup.sh for each selected component.
# If setup.conf defines a `check` command, runs it first; skips the component if it exits 0.
# DOTFILES_DIR is exported so check commands can reference it.
#
# Error recovery strategy: component setup failures warn and continue (non-fatal).
# Framework contract violations (missing functions, bad registry) hard-fail before setup runs.
run_components() {
  [[ ${#SELECTED_COMPONENTS[@]} -eq 0 ]] && return

  local total=${#SELECTED_COMPONENTS[@]} index=1 component label check_cmd

  for component in "${SELECTED_COMPONENTS[@]}"; do
    label=$(conf_get "$WORKBENCH_DIR/$component/setup.conf" label)
    check_cmd=$(conf_get "$WORKBENCH_DIR/$component/setup.conf" check)
    echo -e "\n${DIM}[$index/$total]${NC} ${BOLD}${label:-$component}${NC}"

    # check_cmd is read from setup.conf files that are version-controlled in this repo —
    # treat as trusted input. Do not pass user-supplied strings here.
    # shellcheck disable=SC2086
    if [[ -n "$check_cmd" ]] && bash -c "$check_cmd" > /dev/null 2>&1; then
      success "already configured"
    else
      bash "$WORKBENCH_DIR/$component/setup.sh" || warn "$label setup encountered errors — see above"
    fi

    index=$(( index + 1 ))
  done
}

# print_install_summary — prints the final "All done" screen.
# Per-component summaries are printed inline by each component's setup.sh.
print_install_summary() {
  local shell_name readme
  shell_name=$(basename "$SHELL")
  readme="$WORKBENCH_DIR/README.md"

  echo
  echo -e "${BOLD}${GREEN}━━━ All done ━━━${NC}"
  echo

  echo -e "  ${CYAN}Installed${NC}"
  echo -e "  ${DIM}  • bin scripts   → $LOCAL_BIN_DIR/${NC}"
  echo -e "  ${DIM}  • zsh snippets  → $ZSH_CONFIG_DIR/{framework,tools,aliases,prompt}/${NC}"
  echo -e "  ${DIM}  • gitconfig     → $GITCONFIG_FILE${NC}"
  echo -e "  ${DIM}  • global tasks  → $TASK_CONFIG_DIR/${NC}"
  local component label
  for component in "${SELECTED_COMPONENTS[@]}"; do
    label=$(conf_get "$WORKBENCH_DIR/$component/setup.conf" label)
    echo -e "  ${DIM}  • ${label:-$component}${NC}"
  done

  echo
  echo -e "  ${CYAN}Next steps${NC}"
  echo -e "  ${DIM}  1. Reload your shell:  exec $shell_name${NC}"
  echo -e "  ${DIM}  2. Reference:          $readme${NC}"
  echo
}

# ─── Core installation ────────────────────────────────────────────────────────

echo -e "${BOLD}${BLUE}Installing dotfiles...${NC}"
echo -e "  ${DIM}✓${NC} installed  ${DIM}✓ up to date  ⊘ skipped  ⚠ attention needed${NC}"
echo

step_task_install
echo

# Auto-call sync_<name>() for every core component (steps.sh present, setup.conf absent).
# Optional components (have setup.conf) are handled via the component menu below.
# This mirrors otto-workbench sync — new core components are picked up automatically.
for _f in "$WORKBENCH_DIR"/*/steps.sh; do
  [[ -f "$_f" ]] || continue
  _c=$(basename "$(dirname "$_f")")
  [[ -f "$WORKBENCH_DIR/$_c/setup.conf" ]] && continue
  declare -f "sync_${_c}" > /dev/null && "sync_${_c}"
done
unset _f _c

update_path_in_shell_rc

echo -e "\n${BOLD}${GREEN}✓ Dotfiles installed!${NC}"

# ─── Components ───────────────────────────────────────────────────────────────

REGISTRY="$WORKBENCH_DIR/install.components"
validate_components "$REGISTRY"
discover_components  "$REGISTRY"
select_components
run_components

print_install_summary
