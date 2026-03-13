#!/bin/bash
# Kiro setup steps — sourced by ai/setup.sh, do not run directly.

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
  local dir="$HOME/.kiro/agents"
  mkdir -p "$dir"

  local uvx_path
  uvx_path=$(command -v uvx 2>/dev/null || echo "uvx")

  local file name
  for file in "$SCRIPT_DIR/kiro/agents"/*.json; do
    [[ -e "$file" ]] || continue
    name=$(basename "$file")
    _install_kiro_agent "$dir/$name" "$file" "$uvx_path" "$name"
  done

  echo -e "  ${DIM}Set CONTEXT7_API_KEY in ~/.env.local to enable context7${NC}"
}

step_kiro_rules() {
  local rules_src="$SCRIPT_DIR/guidelines/rules"
  local rules_dst="$HOME/.kiro/steering"
  info "Installing rules to ~/.kiro/steering/"
  mkdir -p "$rules_dst"
  symlink_dir "$rules_src" "$rules_dst" "*.md"
}

register_kiro_steps() {
  register_step "Kiro agent configs" step_kiro_agents
  register_step "Kiro rules"         step_kiro_rules
}

print_kiro_summary() {
  echo
  info "Kiro"
  echo

  local file found

  echo -e "  ${CYAN}Agents${NC} ${DIM}(~/.kiro/agents/)${NC}"
  found=false
  for file in "$HOME/.kiro/agents"/*.json; do
    [[ -e "$file" ]] || continue
    echo -e "  ${DIM}  • $(basename "${file%.json}")${NC}"
    found=true
  done
  if [[ "$found" == false ]]; then echo -e "  ${DIM}  (none)${NC}"; fi
  echo

  echo -e "  ${CYAN}Steering rules${NC} ${DIM}(~/.kiro/steering/)${NC}"
  found=false
  for file in "$HOME/.kiro/steering"/*.md; do
    [[ -e "$file" ]] || continue
    echo -e "  ${DIM}  • $(basename "${file%.md}")${NC}"
    found=true
  done
  if [[ "$found" == false ]]; then echo -e "  ${DIM}  (none)${NC}"; fi
}
