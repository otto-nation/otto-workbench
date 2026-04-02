#!/bin/bash
# Kiro setup steps — sourced by ai/setup.sh and bin/otto-workbench.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# ─── Helpers ──────────────────────────────────────────────────────────────────

# _install_kiro_agent TARGET SOURCE UVX_PATH LABEL
# Processes SOURCE via jq (substituting the uvx path) then writes to TARGET.
# Uses install_file from lib/ui.sh (diff-aware, no prompts) via a temp file.
# context7 reads CONTEXT7_API_KEY from the environment at runtime — no key needed at install time.
_install_kiro_agent() {
  local target=$1 source=$2 uvx_path=$3 label=$4
  local tmp
  tmp=$(mktemp)
  # shellcheck disable=SC2064
  trap "rm -f '$tmp'" RETURN

  jq --arg uvx "$uvx_path" \
    '.mcpServers.serena.command = $uvx' "$source" > "$tmp" \
    || { err "Missing: $source"; return 1; }

  install_file "$tmp" "$target" "$label"
}

# ─── Steps ────────────────────────────────────────────────────────────────────

step_kiro_agents() {
  info "Installing Kiro agent configs"
  mkdir -p "$KIRO_AGENTS_DIR"

  local uvx_path
  uvx_path=$(command -v uvx 2>/dev/null || echo "uvx")

  local file name
  for file in "$KIRO_AGENTS_SRC_DIR"/*.json; do
    [[ -e "$file" ]] || continue
    name=$(basename "$file")
    _install_kiro_agent "$KIRO_AGENTS_DIR/$name" "$file" "$uvx_path" "$name"
  done

  echo -e "  ${DIM}Set CONTEXT7_API_KEY in $ENV_LOCAL_FILE to enable context7${NC}"
}

step_kiro_rules() {
  info "Installing rules to $KIRO_STEERING_DIR/"
  mkdir -p "$KIRO_STEERING_DIR"
  copy_dir "$GUIDELINES_RULES_SRC_DIR" "$KIRO_STEERING_DIR" "$RULES_GLOB" --prune
}

# step_install_kiro — installs kiro-cli via brew if not already in PATH.
step_install_kiro() {
  _ai_install_cask "kiro" "kiro-cli" "kiro-cli" "https://kiro.dev/docs/cli/"
}

register_kiro_steps() {
  register_step "Install kiro-cli"    step_install_kiro
  register_step "Kiro agent configs"  step_kiro_agents
  register_step "Kiro rules"          step_kiro_rules
}

# sync_kiro — runs all Kiro sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
sync_kiro() {
  # Only sync if Kiro was previously set up (steering dir exists).
  # First-time install is handled by ai/setup.sh when the user selects Kiro.
  [[ -d "$KIRO_STEERING_DIR" ]] || return 0
  echo; info "Kiro agents + rules"
  step_kiro_agents
  step_kiro_rules
}

print_kiro_summary() {
  echo
  info "Kiro"
  echo

  local file found

  echo -e "  ${CYAN}Agents${NC} ${DIM}($KIRO_AGENTS_DIR/)${NC}"
  found=false
  for file in "$KIRO_AGENTS_DIR"/*.json; do
    [[ -e "$file" ]] || continue
    echo -e "  ${DIM}  • $(basename "${file%.json}")${NC}"
    found=true
  done
  if [[ "$found" == false ]]; then echo -e "  ${DIM}  (none)${NC}"; fi
  echo

  echo -e "  ${CYAN}Steering rules${NC} ${DIM}($KIRO_STEERING_DIR/)${NC}"
  found=false
  for file in "$KIRO_STEERING_DIR"/*.md; do
    [[ -e "$file" ]] || continue
    echo -e "  ${DIM}  • $(basename "${file%.md}")${NC}"
    found=true
  done
  if [[ "$found" == false ]]; then echo -e "  ${DIM}  (none)${NC}"; fi
}
