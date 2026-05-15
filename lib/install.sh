#!/usr/bin/env bash
# Shared install helpers — component discovery, selection, and execution.
#
# Sourced by both install.sh (bootstrap) and bin/otto-workbench (install command).
# Requires lib/ui.sh to be sourced first (provides constants, prompts, files).

# Guard: constants must be loaded
if [[ -z "${WORKBENCH_DIR:-}" ]]; then
  echo "ERROR: lib/install.sh requires WORKBENCH_DIR (source lib/ui.sh first)" >&2
  return 1 2>/dev/null || exit 1
fi

# ─── PATH management ────────────────────────────────────────────────────────

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

# ─── Platform check ─────────────────────────────────────────────────────────

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

# ─── Optional component system ──────────────────────────────────────────────

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
#
# Reads INSTALL_ALL and INSTALL_TARGETED from the caller's scope.
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
  if [[ "$INSTALL_TARGETED" == true ]]; then
    # Targeted mode: select only named optional components
    for _d in "${eligible_dirs[@]}"; do
      _is_targeted "$_d" && desired+=("$_d")
    done
    [[ ${#desired[@]} -eq 0 ]] && return
  elif [[ "$INSTALL_ALL" == "true" ]]; then
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
  _in_desired() { local _d; for _d in "${desired[@]}"; do [[ "$_d" == "$1" ]] && return 0; done; return 1; }
  _add_missing_deps() {
    local comp="$1" deps dep
    deps=$(conf_get "$WORKBENCH_DIR/$comp/setup.conf" depends)
    [[ -z "$deps" ]] && return
    for dep in $deps; do
      _in_desired "$dep" && continue
      info "Adding $dep (required by $comp)"
      desired+=("$dep")
      _changed=true
    done
  }
  local _changed=true _comp
  while [[ "$_changed" == true ]]; do
    _changed=false
    for _comp in "${desired[@]}"; do
      _add_missing_deps "$_comp"
    done
  done
  unset _changed _comp

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
    export WORKBENCH_CURRENT_COMPONENT="$component"
    label=$(conf_get "$WORKBENCH_DIR/$component/setup.conf" label)
    check_cmd=$(conf_get "$WORKBENCH_DIR/$component/setup.conf" check)
    echo -e "\n${DIM}[$index/$total]${NC} ${BOLD}${label:-$component}${NC}"

    # check_cmd is read from setup.conf files that are version-controlled in this repo —
    # treat as trusted input. Do not pass user-supplied strings here.
    # shellcheck disable=SC2086
    if [[ -n "$check_cmd" ]] && bash -c "$check_cmd" > /dev/null 2>&1; then
      success "already configured"
    else
      "$WORKBENCH_DIR/$component/setup.sh" || warn "$label setup encountered errors — see above"
    fi
    state_record "$component"

    index=$(( index + 1 ))
  done
  unset WORKBENCH_CURRENT_COMPONENT
}

# ─── Core component helpers ─────────────────────────────────────────────────

PREFLIGHT_COMPONENTS=(brew)

# resolve_known_components — builds lookup sets of all known core and optional
# component names. Populates KNOWN_CORE_COMPONENTS and KNOWN_OPTIONAL_COMPONENTS.
KNOWN_CORE_COMPONENTS=()
KNOWN_OPTIONAL_COMPONENTS=()

resolve_known_components() {
  # Core: has steps.sh, no setup.conf, not preflight
  for _f in "$WORKBENCH_DIR"/*/steps.sh; do
    [[ -f "$_f" ]] || continue
    local _c _is_pf=false
    _c=$(basename "$(dirname "$_f")")
    [[ -f "$WORKBENCH_DIR/$_c/setup.conf" ]] && continue
    for _pf in "${PREFLIGHT_COMPONENTS[@]}"; do [[ "$_pf" == "$_c" ]] && { _is_pf=true; break; }; done
    [[ "$_is_pf" == true ]] && continue
    KNOWN_CORE_COMPONENTS+=("$_c")
  done

  # Optional: listed in install.components
  while IFS= read -r _c; do
    [[ -z "$_c" || "$_c" =~ ^# ]] && continue
    KNOWN_OPTIONAL_COMPONENTS+=("$_c")
  done < "$WORKBENCH_DIR/install.components"
}

# validate_install_targets TARGETS... — checks that every target is a known component.
validate_install_targets() {
  local errors=0 target found
  for target in "$@"; do
    found=false
    for _c in "${KNOWN_CORE_COMPONENTS[@]}" "${KNOWN_OPTIONAL_COMPONENTS[@]}"; do
      [[ "$_c" == "$target" ]] && { found=true; break; }
    done
    if [[ "$found" == false ]]; then
      err "Unknown component: '$target'"
      errors=$(( errors + 1 ))
    fi
  done
  if (( errors > 0 )); then
    echo
    echo "Known core components:     ${KNOWN_CORE_COMPONENTS[*]}"
    echo "Known optional components: ${KNOWN_OPTIONAL_COMPONENTS[*]}"
    exit 1
  fi
}

# discover_core_components — finds core component dirs and their descriptions.
# Populates the nameref arrays with dirs and descriptions.
# shellcheck disable=SC2178  # namerefs
discover_core_components() {
  local -n __dirs=$1 __descs=$2
  __dirs=()
  __descs=()

  for _f in "$WORKBENCH_DIR"/*/steps.sh; do
    [[ -f "$_f" ]] || continue
    local _c _is_preflight=false
    _c=$(basename "$(dirname "$_f")")
    [[ -f "$WORKBENCH_DIR/$_c/setup.conf" ]] && continue
    for _pf in "${PREFLIGHT_COMPONENTS[@]}"; do [[ "$_pf" == "$_c" ]] && { _is_preflight=true; break; }; done
    [[ "$_is_preflight" == true ]] && continue
    __dirs+=("$_c")
    # Parse '# description: ...' from the first 5 lines of steps.sh
    local _desc=""
    while IFS= read -r _line; do
      [[ "$_line" =~ ^#[[:space:]]*description:[[:space:]]*(.*) ]] || continue
      _desc="${BASH_REMATCH[1]}"
      break
    done < <(head -n 5 "$_f")
    __descs+=("$_desc")
  done
}

# select_core_components RESULT_ARRAY DIRS_ARRAY DESCS_ARRAY — presents selection menu
# for core components. Reads INSTALL_ALL, INSTALL_TARGETED, INSTALL_TARGETS from caller.
# shellcheck disable=SC2178  # namerefs
select_core_components() {
  local -n __out=$1
  local -n __dirs=$2
  local -n __descs=$3
  __out=()

  if [[ "$INSTALL_TARGETED" == true ]]; then
    for _c in "${__dirs[@]}"; do
      _is_targeted "$_c" && __out+=("$_c")
    done
  elif [[ "$INSTALL_ALL" == "true" ]]; then
    __out=("${__dirs[@]}")
  else
    info "Core components:"
    for _i in "${!__dirs[@]}"; do
      printf "  [%d] %-22s ${DIM}%s${NC}\n" "$(( _i + 1 ))" "${__dirs[$_i]}" "${__descs[$_i]}"
    done
    echo
    local _core_sel=""
    select_menu _core_sel "${#__dirs[@]}" --default all
    local _num
    for _num in $_core_sel; do
      __out+=("${__dirs[$(( _num - 1 ))]}")
    done
  fi
}

# run_core_component COMPONENT — runs the install or sync function for a core component.
run_core_component() {
  local _c="$1"
  export WORKBENCH_CURRENT_COMPONENT="$_c"
  if declare -f "install_${_c}" > /dev/null; then
    "install_${_c}"
  elif declare -f "sync_${_c}" > /dev/null; then
    "sync_${_c}"
  fi
  state_record "$_c"
}

# ─── Target helpers ──────────────────────────────────────────────────────────

# _is_targeted TARGET — returns 0 if TARGET is in INSTALL_TARGETS.
# Reads INSTALL_TARGETS from caller's scope.
_is_targeted() {
  local target=$1 t
  for t in "${INSTALL_TARGETS[@]}"; do [[ "$t" == "$target" ]] && return 0; done
  return 1
}

# parse_install_flags ARGS... — parses --all and component targets.
# Sets INSTALL_ALL, INSTALL_TARGETS, INSTALL_TARGETED in caller's scope.
parse_install_flags() {
  INSTALL_ALL=false
  INSTALL_TARGETS=()
  INSTALL_TARGETED=false

  for _arg in "$@"; do
    case "$_arg" in
      --all) INSTALL_ALL=true ;;
      -*)    err "Unknown flag: $_arg"; exit 1 ;;
      *)     INSTALL_TARGETS+=("$_arg") ;;
    esac
  done
  [[ ${#INSTALL_TARGETS[@]} -gt 0 ]] && INSTALL_TARGETED=true
}

# ─── Install summary ────────────────────────────────────────────────────────

# print_install_summary — prints the final "All done" screen with a
# consolidated file listing, editable configs, and per-component summaries.
print_install_summary() {
  . "$WORKBENCH_DIR/lib/summary.sh"

  echo
  echo -e "${BOLD}${GREEN}━━━ All done ━━━${NC}"
  print_workbench_summary

  # Run per-component summaries for selected components (brew, docker, ai, etc.)
  run_component_summaries "${SELECTED_COMPONENTS[@]}"

  print_warnings_summary
}
