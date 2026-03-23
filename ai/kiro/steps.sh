#!/bin/bash
# Kiro setup steps — sourced by ai/setup.sh and bin/otto-workbench.

# Derive the ai/ directory from this file's own location so callers don't
# need to inject SCRIPT_DIR. Works whether sourced or executed directly.
_AI_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ─── Helpers ──────────────────────────────────────────────────────────────────

# _install_file TARGET CONTENT LABEL
# Writes CONTENT to TARGET, prompting before overwriting an existing file.
_install_file() {
  local target=$1 content=$2 label=$3
  if [[ -f "$target" ]]; then
    prompt_overwrite "$target" || { skip; return; }
  fi
  printf '%s\n' "$content" > "$target"
  success "Wrote $label"
}

# _install_kiro_agent TARGET SOURCE UVX_PATH LABEL
# Processes SOURCE via jq (substituting the uvx path) then writes to TARGET.
# context7 reads CONTEXT7_API_KEY from the environment at runtime — no key needed at install time.
_install_kiro_agent() {
  local target=$1 source=$2 uvx_path=$3 label=$4
  local content
  content=$(jq --arg uvx "$uvx_path" \
    '.mcpServers.serena.command = $uvx' "$source") \
    || { err "Missing: $source"; return 1; }
  _install_file "$target" "$content" "$label"
}

# ─── Steps ────────────────────────────────────────────────────────────────────

step_kiro_agents() {
  info "Installing Kiro agent configs"
  local dir="$KIRO_AGENTS_DIR"
  mkdir -p "$dir"

  local uvx_path
  uvx_path=$(command -v uvx 2>/dev/null || echo "uvx")

  local file name
  for file in "$_AI_DIR/kiro/agents"/*.json; do
    [[ -e "$file" ]] || continue
    name=$(basename "$file")
    _install_kiro_agent "$dir/$name" "$file" "$uvx_path" "$name"
  done

  echo -e "  ${DIM}Set CONTEXT7_API_KEY in $ENV_LOCAL_FILE to enable context7${NC}"
}

step_kiro_rules() {
  local rules_src="$_AI_DIR/guidelines/rules"
  local rules_dst="$KIRO_STEERING_DIR"
  info "Installing rules to $KIRO_STEERING_DIR/"
  mkdir -p "$rules_dst"
  symlink_dir "$rules_src" "$rules_dst" "*.md"
}

register_kiro_steps() {
  register_step "Kiro agent configs" step_kiro_agents
  register_step "Kiro rules"         step_kiro_rules
}

# sync_kiro — runs all Kiro sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
sync_kiro() {
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
