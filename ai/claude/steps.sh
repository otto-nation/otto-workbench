#!/bin/bash
# Claude Code setup steps — sourced by ai/setup.sh, do not run directly.

# ─── Helpers ──────────────────────────────────────────────────────────────────

# _mcp_is_registered NAME — returns 0 if NAME is in the user-scope MCP list.
_mcp_is_registered() {
  claude mcp list 2>/dev/null | grep -q "^${1}"
}

# _mcp_install NAME COMMAND... — adds an MCP server at user scope if not already registered.
_mcp_install() {
  local name="$1"; shift
  if _mcp_is_registered "$name"; then
    success "$name already registered"
    return
  fi
  info "Installing $name (user scope)"
  claude mcp add "$name" --scope user -- "$@"
  success "$name installed"
}

# _install_claude_symlink SOURCE TARGET LABEL
# Creates or updates a symlink at TARGET. Existing symlinks are silently updated;
# real files/dirs prompt before overwrite. Uses -h to prevent BSD ln from
# dereferencing an existing directory symlink on re-runs.
_install_claude_symlink() {
  local source=$1 target=$2 label=$3

  if [[ -L "$target" ]]; then
    ln -sfh "$source" "$target"
    success "$label (updated symlink)"
  elif [[ -e "$target" ]]; then
    if confirm_n "$target already exists. Overwrite with symlink?"; then
      rm -rf "$target"
      ln -sfh "$source" "$target"
      success "$label"
    else
      skip
    fi
  else
    ln -sfh "$source" "$target"
    success "$label"
  fi
}

_install_workbench_rule() {
  local workbench_path
  workbench_path="$(cd "$SCRIPT_DIR/.." && pwd)"
  local target="$HOME/.claude/rules/workbench.md"

  mkdir -p "$HOME/.claude/rules"
  cat > "$target" <<EOF
# Workbench

Your developer environment is managed at: $workbench_path

What it owns: Claude config (\`ai/claude/\`), coding rules (\`ai/guidelines/rules/\`),
bin scripts (\`bin/\`), zsh config (\`zsh/\`), git config (\`git/\`),
Docker setup (\`docker/\`), Brew packages (\`brew/\`).

When modifying Claude config (MCPs, agents, skills, settings), bin scripts, zsh config,
git config, or developer tooling — make the change in the workbench repo and re-run
the relevant setup script. Do not edit \`~/\` directly.

Re-run AI setup:   bash $workbench_path/ai/setup.sh
Add a local rule:  claude-rules add <domain> "rule text"
Edit local rules:  claude-rules open [domain]
Review untracked:  claude-rules status
EOF
  success "workbench.md"
}

# _check_local_rules DIR — warns about any *.local.md files found in DIR that
# are not tracked in the workbench. Called at the end of step_claude_rules.
_check_local_rules() {
  local rules_dst="$1" found=false file lines
  for file in "$rules_dst"/*.local.md; do
    [[ -e "$file" ]] || continue
    lines=$(wc -l < "$file" | tr -d ' ')
    if [[ "$found" == false ]]; then
      echo
      warn "Local rule additions not tracked in workbench:"
    fi
    echo -e "  ${DIM}  • $(basename "$file") ($lines lines)${NC}"
    found=true
  done
  if [[ "$found" == true ]]; then
    echo -e "  ${DIM}  Run 'claude-rules status' to review, or edit $SCRIPT_DIR/guidelines/rules/ to formalize${NC}"
  fi
}

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_claude_mcps — installs all MCP servers discovered from ai/claude/mcps/*.json.
# Each manifest declares the MCP name (filename without .json), a display label,
# the install command array, and an optional post-install note.
step_claude_mcps() {
  local mcp_dir="$SCRIPT_DIR/claude/mcps"
  [[ -d "$mcp_dir" ]] || { warn "No MCP configs found in $mcp_dir — skipping"; return; }

  local file name note
  local cmd_args=()
  for file in "$mcp_dir"/*.json; do
    [[ -e "$file" ]] || continue
    name=$(basename "$file" .json)
    local url
    url=$(jq -r '.url // empty' "$file")
    [[ -z "$url" ]] && { err "$name: manifest is missing required field: url"; return 1; }
    echo -e "  ${DIM}$url${NC}"
    cmd_args=()
    while IFS= read -r arg; do
      cmd_args+=("$arg")
    done < <(jq -r '.command[]' "$file")
    _mcp_install "$name" "${cmd_args[@]}"
    note=$(jq -r '.note // empty' "$file")
    [[ -n "$note" ]] && echo -e "  ${DIM}$note${NC}"
  done
}

step_claude_guidelines() {
  local src="$SCRIPT_DIR/claude/CLAUDE.md"
  [[ -f "$src" ]] || { err "Missing $src"; return 1; }
  mkdir -p "$HOME/.claude"
  _install_claude_symlink "$src" "$HOME/.claude/CLAUDE.md" "CLAUDE.md"
}

step_claude_rules() {
  local rules_src="$SCRIPT_DIR/guidelines/rules"
  local rules_dst="$HOME/.claude/rules"
  [[ -d "$rules_src" ]] || { warn "No rules found in $rules_src — skipping"; return; }

  mkdir -p "$rules_dst"
  info "Installing rules to ~/.claude/rules/"
  local file
  for file in "$rules_src"/*.md; do
    [[ -e "$file" ]] || continue
    _install_claude_symlink "$file" "$rules_dst/$(basename "$file")" "$(basename "${file%.md}")"
  done

  echo
  info "Generating workbench.md"
  _install_workbench_rule

  _check_local_rules "$rules_dst"
}

step_claude_settings() {
  local src="$SCRIPT_DIR/claude/settings.json"
  local target="$HOME/.claude/settings.json"
  local filter="$SCRIPT_DIR/claude/sync-settings.jq"

  mkdir -p "$HOME/.claude"

  local existing="{}" content
  if [[ -f "$target" ]]; then
    content=$(cat "$target")
    [[ -n "$content" ]] && existing="$content"
  fi

  local result
  result=$(jq -n --argjson t "$(cat "$src")" --argjson e "$existing" -f "$filter") \
    || { err "Failed to sync settings.json"; return 1; }

  printf '%s\n' "$result" > "$target"
  if [[ "$existing" == "{}" ]]; then success "settings.json written"; else success "settings.json synced"; fi
}

step_claude_skills() {
  local src="$SCRIPT_DIR/claude/skills" dst="$HOME/.claude/skills"
  [[ -d "$src" ]] || { warn "No skills found in $src — skipping"; return; }
  mkdir -p "$dst"
  info "Installing Claude Code skills to ~/.claude/skills/"
  local dir
  for dir in "$src"/*/; do
    _install_claude_symlink "$dir" "$dst/$(basename "$dir")" "$(basename "$dir")"
  done
}

step_claude_agents() {
  local src="$SCRIPT_DIR/claude/agents" dst="$HOME/.claude/agents"
  [[ -d "$src" ]] || { warn "No agents found in $src — skipping"; return; }
  mkdir -p "$dst"
  info "Installing Claude Code agents to ~/.claude/agents/"
  local file name
  for file in "$src"/*.md; do
    [[ -e "$file" ]] || continue
    name=$(basename "$file")
    _install_claude_symlink "$file" "$dst/$name" "${name%.md}"
  done
}

register_claude_steps() {
  if ! command -v claude >/dev/null 2>&1; then
    warn "Claude Code (claude) not found in PATH — skipping Claude setup steps"
    return
  fi
  register_step "Claude Code settings"    step_claude_settings
  register_step "Claude Code guidelines"  step_claude_guidelines
  register_step "Claude Code rules"       step_claude_rules
  register_step "MCP servers"             step_claude_mcps
  register_step "Claude Code skills"      step_claude_skills
  register_step "Claude Code agents"      step_claude_agents
}

print_claude_summary() {
  echo
  info "Claude Code configuration summary"
  echo

  echo -e "  ${CYAN}Agents${NC} ${DIM}(~/.claude/agents/)${NC}"
  local file found=false
  for file in "$HOME/.claude/agents"/*.md; do
    [[ -e "$file" ]] || continue
    echo -e "  ${DIM}  • $(basename "${file%.md}")${NC}"
    found=true
  done
  [[ "$found" == false ]] && echo -e "  ${DIM}  (none installed)${NC}"
  echo

  echo -e "  ${CYAN}MCP servers${NC} ${DIM}(user scope — run: claude mcp list)${NC}"
  echo
  echo -e "  ${CYAN}~/.claude/settings.json${NC} ${DIM}— permissions, deny rules, plugin config${NC}"
  echo -e "  ${CYAN}~/.claude/CLAUDE.md${NC} ${DIM}— persistent coding guidelines${NC}"
}
