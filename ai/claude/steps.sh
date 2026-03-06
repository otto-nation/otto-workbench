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

_install_claude_guidelines() {
  local general_content=$1 lang_content=$2
  local target="$HOME/.claude/CLAUDE.md"
  local combined="${general_content}

---

${lang_content}"

  mkdir -p "$HOME/.claude"

  if [[ ! -f "$target" ]]; then
    printf '%s\n' "$combined" > "$target"
    success "Guidelines written to ~/.claude/CLAUDE.md"
    return
  fi

  echo
  warn "$HOME/.claude/CLAUDE.md already exists. What would you like to do?"
  echo "  [1] Backup and overwrite  (~/.claude/CLAUDE.md.backup)"
  echo "  [2] Append to existing"
  echo "  [3] Skip"
  echo
  read -r -n 1 -p "  Choice [1/2/3]: " choice
  echo

  case $choice in
    1)
      cp "$target" "${target}.backup"
      success "Backed up to ~/.claude/CLAUDE.md.backup"
      printf '%s\n' "$combined" > "$target"
      success "Guidelines written to ~/.claude/CLAUDE.md"
      ;;
    2)
      printf '\n\n---\n\n%s\n' "$combined" >> "$target"
      success "Guidelines appended to ~/.claude/CLAUDE.md"
      ;;
    *)
      skip
      ;;
  esac
}

# ─── Steps ────────────────────────────────────────────────────────────────────

step_mcp_serena() {
  _mcp_install serena \
    uvx --from git+https://github.com/oraios/serena serena start-mcp-server --context ide-assistant
}

step_mcp_sequential_thinking() {
  _mcp_install sequential-thinking \
    npx -y @modelcontextprotocol/server-sequential-thinking
}

step_mcp_context7() {
  _mcp_install context7 npx -y @upstash/context7-mcp
  echo -e "  ${DIM}Set CONTEXT7_API_KEY in ~/.env.local to enable${NC}"
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
  [[ "$existing" == "{}" ]] && success "settings.json written" || success "settings.json synced"
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
