#!/usr/bin/env bash
# Post-run summary for install.sh and otto-workbench sync.
#
# Prints a consolidated view of managed files, editable configs, and quick
# reference commands. Sourced by install.sh and bin/otto-workbench.
#
# All path variables come from lib/constants.sh (sourced via lib/ui.sh).

# ── Environment setup from registries ──────────────────────────────────────────

# shellcheck source=registries.sh
. "$WORKBENCH_DIR/lib/registries.sh"

# _env_ensure_header — prints the "Environment setup" header on first call.
# Uses dynamic scoping: reads/writes `_env_found` from the calling function.
_env_ensure_header() {
  [[ "$_env_found" == true ]] && return
  echo
  echo -e "  ${CYAN}Environment setup${NC}"
  echo -e "  ${DIM}  Add these to $ENV_LOCAL_FILE:${NC}"
  _env_found=true
}

# _env_print_var VAR PREFIX DEFAULT SETUP_URL — prints a single env var line.
_env_print_var() {
  local var="$1" prefix="$2" default_val="$3" setup_url="$4"
  _env_ensure_header

  local val=""
  if [[ -n "$prefix" && "$prefix" != "null" ]]; then
    val="$prefix"
  elif [[ -n "$default_val" && "$default_val" != "null" ]]; then
    val="$default_val"
  fi
  echo -e "  ${DIM}  export ${var}=${val}${NC}"
  if [[ -n "$setup_url" && "$setup_url" != "null" ]]; then
    echo -e "  ${DIM}    → $setup_url${NC}"
  fi
}

# _env_setup_entry — callback for iter_registry_env.
_env_setup_entry() {
  local var="$1" _comment="$2" default_val="$3" setup_url="$4" prefix="$5"
  _env_print_var "$var" "$prefix" "$default_val" "$setup_url"
}

# _env_setup_auth_entry — callback for iter_registry_auth.
_env_setup_auth_entry() {
  local _name="$1" env_var="$2" setup_url="$3" prefix="$4"
  _env_print_var "$env_var" "$prefix" "" "$setup_url"
}

# _print_env_setup — prints env setup instructions from all registries.
# Scans all registries and shows env vars the user may need to configure.
# Respects install_check: skips registries/tools not installed or not active.
_print_env_setup() {
  # yq is required for registry iteration; skip silently if not available
  command -v yq >/dev/null 2>&1 || return 0

  local _env_found=false
  local -a registries=()
  collect_registries registries "$WORKBENCH_DIR"

  local reg
  for reg in "${registries[@]}"; do
    registry_passes_install_check "$reg" || continue
    iter_registry_env "$reg" _env_setup_entry
    iter_registry_auth "$reg" _env_setup_auth_entry
  done
}

# ── Main summary ──────────────────────────────────────────────────────────────

# print_workbench_summary — prints the consolidated summary of what the
# workbench manages and what the user can edit.
print_workbench_summary() {
  local home_short="~"

  # ── Workbench location ───────────────────────────────────────────────
  echo
  echo -e "  ${CYAN}Workbench${NC}    ${DIM}${WORKBENCH_DIR/#"$HOME"/$home_short}${NC}"

  # ── Managed files ────────────────────────────────────────────────────
  echo
  echo -e "  ${CYAN}Managed files${NC}"

  # bin scripts — count symlinks pointing into our bin source dir
  local bin_count=0
  if [[ -d "$LOCAL_BIN_DIR" ]]; then
    local item target
    for item in "$LOCAL_BIN_DIR"/*; do
      [[ -L "$item" ]] || continue
      target=$(readlink "$item" 2>/dev/null)
      [[ "$target" == "$BIN_SRC_DIR"/* ]] && bin_count=$(( bin_count + 1 ))
    done
  fi
  echo -e "  ${DIM}  bin scripts       ${LOCAL_BIN_DIR/#"$HOME"/$home_short}/ ($bin_count scripts)${NC}"

  # zsh snippets — list layer dirs
  local layers=""
  if [[ -d "$ZSH_CONFIG_DIR" ]]; then
    local layer
    for layer in "$ZSH_CONFIG_DIR"/*/; do
      [[ -d "$layer" ]] || continue
      layers+="$(basename "$layer"),"
    done
    layers="${layers%,}"
  fi
  if [[ -n "$layers" ]]; then
    echo -e "  ${DIM}  zsh snippets      ${ZSH_CONFIG_DIR/#"$HOME"/$home_short}/{${layers}}/  ${NC}"
  fi
  echo -e "  ${DIM}  zsh loader        ${ZSH_LOADER_DST/#"$HOME"/$home_short}${NC}"
  echo -e "  ${DIM}  starship          ${STARSHIP_CONFIG_FILE/#"$HOME"/$home_short}${NC}"
  local shared_rel="${GIT_SHARED_CONFIG#"$WORKBENCH_DIR/"}"
  echo -e "  ${DIM}  git shared        ${GITCONFIG_FILE/#"$HOME"/$home_short} includes ${shared_rel}${NC}"
  echo -e "  ${DIM}  git hooks         ${GIT_HOOKS_DIR/#"$HOME"/$home_short}/{pre-commit,pre-push}${NC}"
  echo -e "  ${DIM}  global Taskfile   ${TASK_CONFIG_DIR/#"$HOME"/$home_short}/{Taskfile.yml,lib/}${NC}"

  # Claude — only if installed
  if [[ -d "$CLAUDE_DIR" ]]; then
    echo -e "  ${DIM}  Claude config     ${CLAUDE_DIR/#"$HOME"/$home_short}/{settings.json,CLAUDE.md,rules/,skills/,agents/}${NC}"
  fi

  # ── Editable configs ─────────────────────────────────────────────────
  echo
  echo -e "  ${CYAN}Editable configs${NC} ${DIM}(never overwritten by sync)${NC}"

  # git identity — check if [user] section has a real name configured
  local _git_status="${DIM}needs setup${NC}"
  if [[ -f "$GITCONFIG_FILE" ]]; then
    local _git_name
    _git_name=$(git config --global user.name 2>/dev/null || true)
    if [[ -n "$_git_name" && "$_git_name" != *"CHANGEME"* && "$_git_name" != *"Your Name"* ]]; then
      _git_status="${DIM}${_git_name}${NC}"
    fi
  fi
  echo -e "  ${DIM}  git identity      ${GITCONFIG_FILE/#"$HOME"/$home_short}  ${NC}${_git_status}"

  # shell secrets — check if file exists and count configured vars
  local _env_status="${YELLOW}not created${NC} ${DIM}— see ${ENV_LOCAL_FILE/#"$HOME"/$home_short}.template${NC}"
  if [[ -f "$ENV_LOCAL_FILE" ]]; then
    local _env_count
    _env_count=$(grep -c '^export ' "$ENV_LOCAL_FILE" 2>/dev/null) || _env_count=0
    _env_status="${DIM}${_env_count} var(s) configured${NC}"
  fi
  echo -e "  ${DIM}  shell secrets     ${ENV_LOCAL_FILE/#"$HOME"/$home_short}  ${NC}${_env_status}"

  # AI tokens — check AI_COMMAND and GH_TOKEN in taskfile.env
  local _ai_status="${YELLOW}not configured${NC} ${DIM}— run: task --global ai:setup${NC}"
  local _gh_status="${YELLOW}not set${NC}"
  if [[ -f "$TASKFILE_ENV" ]]; then
    local _ai_cmd
    _ai_cmd=$(grep -m1 '^AI_COMMAND=' "$TASKFILE_ENV" 2>/dev/null | sed 's/^AI_COMMAND=//')
    [[ -n "$_ai_cmd" ]] && _ai_status="${DIM}${_ai_cmd}${NC}"
    grep -q '^GH_TOKEN=' "$TASKFILE_ENV" 2>/dev/null && _gh_status="${DIM}configured${NC}"
  fi
  echo -e "  ${DIM}  AI command        ${TASKFILE_ENV/#"$HOME"/$home_short}  ${NC}${_ai_status}"
  echo -e "  ${DIM}  GH_TOKEN          ${TASKFILE_ENV/#"$HOME"/$home_short}  ${NC}${_gh_status}"

  echo -e "  ${DIM}  shell rc          ${ZSHRC_FILE/#"$HOME"/$home_short}${NC}"

  # Ghostty — only if config exists
  if [[ -f "$GHOSTTY_CONFIG_FILE" ]]; then
    echo -e "  ${DIM}  terminal          ${GHOSTTY_CONFIG_FILE/#"$HOME"/$home_short}${NC}"
  fi

  # ── Environment setup (from registries) ──────────────────────────────
  _print_env_setup

  # ── Quick reference ──────────────────────────────────────────────────
  echo
  echo -e "  ${CYAN}Quick reference${NC}"
  echo -e "  ${DIM}  Sync config       otto-workbench sync${NC}"
  echo -e "  ${DIM}  Add a rule        claude-rules add <domain> \"rule\"${NC}"
  echo -e "  ${DIM}  Reload shell      exec $(basename "${SHELL:-zsh}")${NC}"
  echo
}

# run_component_summaries [COMPONENT...] — auto-discovers and calls print_<name>_summary()
# from */summary.sh files. If COMPONENT args are given, only those are checked;
# otherwise all components with summary.sh are discovered via glob.
run_component_summaries() {
  local -a files=()

  if [[ $# -eq 0 ]]; then
    # Auto-discover all components with summary.sh (skip lib/ — it's not a component)
    for _f in "$WORKBENCH_DIR"/*/summary.sh; do
      [[ -f "$_f" && "$(dirname "$_f")" != "$WORKBENCH_DIR/lib" ]] && files+=("$_f")
    done
  else
    for _c in "$@"; do
      files+=("$WORKBENCH_DIR/$_c/summary.sh")
    done
  fi

  local summary_file fn
  for summary_file in "${files[@]}"; do
    [[ -f "$summary_file" ]] || continue
    # shellcheck source=/dev/null
    . "$summary_file"
    fn="print_$(basename "$(dirname "$summary_file")")_summary"
    declare -f "$fn" > /dev/null 2>&1 && "$fn" || true
  done
}
