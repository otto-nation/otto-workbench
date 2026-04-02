#!/bin/bash
# Post-run summary for install.sh and otto-workbench sync.
#
# Prints a consolidated view of managed files, editable configs, and quick
# reference commands. Sourced by install.sh and bin/otto-workbench.
#
# All path variables come from lib/constants.sh (sourced via lib/ui.sh).

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

  # Kiro — only if installed
  if [[ -d "$KIRO_STEERING_DIR" ]]; then
    local kiro_dir="${KIRO_STEERING_DIR%/steering}"
    echo -e "  ${DIM}  Kiro config       ${kiro_dir/#"$HOME"/$home_short}/{agents/,steering/}${NC}"
  fi

  # ── Editable configs ─────────────────────────────────────────────────
  echo
  echo -e "  ${CYAN}Editable configs${NC} ${DIM}(never overwritten by sync)${NC}"
  echo -e "  ${DIM}  git identity      ${GITCONFIG_FILE/#"$HOME"/$home_short}${NC}"
  echo -e "  ${DIM}  shell secrets     ${ENV_LOCAL_FILE/#"$HOME"/$home_short}${NC}"
  echo -e "  ${DIM}  AI tokens         ${TASKFILE_ENV/#"$HOME"/$home_short}${NC}"
  echo -e "  ${DIM}  shell rc          ${ZSHRC_FILE/#"$HOME"/$home_short}${NC}"

  # Ghostty — only if config exists
  if [[ -f "$GHOSTTY_CONFIG_FILE" ]]; then
    echo -e "  ${DIM}  terminal          ${GHOSTTY_CONFIG_FILE/#"$HOME"/$home_short}${NC}"
  fi

  # ── Quick reference ──────────────────────────────────────────────────
  echo
  echo -e "  ${CYAN}Quick reference${NC}"
  echo -e "  ${DIM}  Sync config       otto-workbench sync${NC}"
  echo -e "  ${DIM}  Add a rule        claude-rules add <domain> \"rule\"${NC}"
  echo -e "  ${DIM}  Reload shell      exec $(basename "${SHELL:-zsh}")${NC}"
  echo
}
