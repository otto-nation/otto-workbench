#!/bin/bash

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
. "$SCRIPT_DIR/../lib/ui.sh"

# ─── Prompt helpers ───────────────────────────────────────────────────────────

# prompt_secret "label" var — hidden read into named variable
prompt_secret() {
  local label=$1
  local var=$2
  local value
  read -rsp "${label}: " value
  echo
  printf -v "$var" '%s' "$value"
}

# ─── Tool selection ───────────────────────────────────────────────────────────
SELECTED_TOOLS=()

tool_selected() {
  # Bash arrays have no built-in membership test; iterate to check for a match
  local tool=$1
  local t
  for t in "${SELECTED_TOOLS[@]}"; do
    [[ "$t" == "$tool" ]] && return 0
  done
  return 1
}

select_tools() {
  echo -e "${BOLD}${BLUE}AI Tools Setup${NC}\n"
  echo "Which AI tools do you want to set up?"
  echo "  [1] Claude Code"
  echo "  [2] Kiro"
  echo
  read -rp "Space-separated numbers (e.g. \"1 2\"): " selection
  echo

  local num
  for num in $selection; do
    case $num in
      1) SELECTED_TOOLS+=("claude") ;;
      2) SELECTED_TOOLS+=("kiro") ;;
      *) warn "Unknown option: $num — ignored" ;;
    esac
  done

  if [[ ${#SELECTED_TOOLS[@]} -eq 0 ]]; then
    err "No tools selected. Exiting."
    exit 0
  fi

  echo -ne "Setting up: "
  local t
  for t in "${SELECTED_TOOLS[@]}"; do
    echo -ne "${BOLD}${t}${NC}  "
  done
  echo
}

# ─── Step registration ────────────────────────────────────────────────────────
STEPS=()

register_step() {
  local name=$1
  local fn=$2
  STEPS+=("${name}|${fn}")
}

# ─── Step: MCP — Serena ───────────────────────────────────────────────────────
step_mcp_serena() {
  info "Installing Serena MCP server (user scope)"
  claude mcp add serena --scope user -- \
    uvx --from git+https://github.com/oraios/serena serena start-mcp-server --context ide-assistant
  success "Serena MCP installed"
}

# ─── Step: MCP — Sequential Thinking ─────────────────────────────────────────
step_mcp_sequential_thinking() {
  info "Installing Sequential Thinking MCP server (user scope)"
  claude mcp add sequential-thinking --scope user -- \
    npx -y @modelcontextprotocol/server-sequential-thinking
  success "Sequential Thinking MCP installed"
}

# ─── Step: MCP — Context7 ────────────────────────────────────────────────────
step_mcp_context7() {
  info "Installing Context7 MCP server (user scope)"
  echo
  warn "Context7 requires an Upstash API key (leave blank to skip)."
  local api_key=""
  prompt_secret "Upstash API key" api_key

  if [[ -z "$api_key" ]]; then
    skip
    return
  fi

  claude mcp add --scope user context7 -- \
    npx -y @upstash/context7-mcp --api-key "$api_key"
  success "Context7 MCP installed"
}

# ─── Step: Guidelines (shared — installs to all selected tools) ───────────────
step_guidelines() {
  info "Downloading AI coding guidelines"

  local general_url="https://gist.githubusercontent.com/isaacgarza/f72abdf85a8a30dad9476ab93049a362/raw/ai-coding-guidelines-general.md"
  local lang_url="https://gist.githubusercontent.com/isaacgarza/f72abdf85a8a30dad9476ab93049a362/raw/ai-coding-guidelines-language-specific.md"

  local general_content lang_content
  general_content=$(curl -fsSL "$general_url") || { err "Failed to download general guidelines"; return 1; }
  lang_content=$(curl -fsSL "$lang_url") || { err "Failed to download language-specific guidelines"; return 1; }

  if tool_selected "claude"; then
    _install_claude_guidelines "$general_content" "$lang_content"
  fi

  if tool_selected "kiro"; then
    _install_kiro_guidelines "$general_content" "$lang_content"
  fi
}

_install_claude_guidelines() {
  local general_content=$1
  local lang_content=$2
  local target="$HOME/.claude/CLAUDE.md"
  local combined="${general_content}

---

${lang_content}"

  mkdir -p "$HOME/.claude"

  if [[ -f "$target" ]]; then
    echo
    warn "~/.claude/CLAUDE.md already exists. What would you like to do?"
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
  else
    printf '%s\n' "$combined" > "$target"
    success "Guidelines written to ~/.claude/CLAUDE.md"
  fi
}

_install_kiro_guidelines() {
  local general_content=$1
  local lang_content=$2
  local steering_dir="$HOME/.kiro/steering"

  mkdir -p "$steering_dir"
  _install_file "$steering_dir/general.md" "$general_content" "general.md"
  _install_file "$steering_dir/language-specific.md" "$lang_content" "language-specific.md"
}

_install_file() {
  local target=$1
  local content=$2
  local label=$3

  if [[ -f "$target" ]]; then
    if confirm_n "$target already exists. Overwrite?"; then
      printf '%s\n' "$content" > "$target"
      success "Wrote $label"
    else
      skip
    fi
  else
    printf '%s\n' "$content" > "$target"
    success "Wrote $label"
  fi
}

# ─── Step: Claude Code skills ────────────────────────────────────────────────
step_claude_skills() {
  local skills_src="$SCRIPT_DIR/claude/skills"
  local skills_dst="$HOME/.claude/skills"

  if [[ ! -d "$skills_src" ]]; then
    warn "No skills found in $skills_src — skipping"
    return
  fi

  mkdir -p "$skills_dst"
  info "Installing Claude Code skills to ~/.claude/skills/"

  local skill
  for skill_dir in "$skills_src"/*/; do
    skill=$(basename "$skill_dir")
    local target="$skills_dst/$skill"

    if [[ -L "$target" ]]; then
      ln -sf "$skill_dir" "$target"
      success "$skill (updated symlink)"
    elif [[ -d "$target" ]]; then
      if confirm_n "~/.claude/skills/$skill already exists as a real directory. Overwrite with symlink?"; then
        rm -rf "$target"
        ln -sf "$skill_dir" "$target"
        success "$skill"
      else
        skip
      fi
    else
      ln -sf "$skill_dir" "$target"
      success "$skill"
    fi
  done
}

# ─── Step: Claude Code agents ────────────────────────────────────────────────
step_claude_agents() {
  local agents_src="$SCRIPT_DIR/claude/agents"
  local agents_dst="$HOME/.claude/agents"

  if [[ ! -d "$agents_src" ]]; then
    warn "No agent definitions found in $agents_src — skipping"
    return
  fi

  mkdir -p "$agents_dst"
  info "Installing Claude Code agents to ~/.claude/agents/"

  local agent_file agent_name
  for agent_file in "$agents_src"/*.md; do
    [[ -e "$agent_file" ]] || continue
    agent_name=$(basename "$agent_file")
    local target="$agents_dst/$agent_name"

    if [[ -L "$target" ]]; then
      ln -sf "$agent_file" "$target"
      success "${agent_name%.md} (updated symlink)"
    elif [[ -f "$target" ]]; then
      if confirm_n "~/.claude/agents/$agent_name already exists. Overwrite with symlink?"; then
        rm -f "$target"
        ln -sf "$agent_file" "$target"
        success "${agent_name%.md}"
      else
        skip
      fi
    else
      ln -sf "$agent_file" "$target"
      success "${agent_name%.md}"
    fi
  done
}

# ─── Step: Claude Code agent info ────────────────────────────────────────────
step_claude_agent_info() {
  echo
  info "Claude Code configuration summary"
  echo
  echo -e "  ${CYAN}Agents${NC} ${DIM}(~/.claude/agents/)${NC}"
  echo -e "  ${DIM}  • ci-cd  — commit message and PR generation (used by task automation)${NC}"
  echo
  echo -e "  ${CYAN}MCP servers${NC} ${DIM}(user scope)${NC}"
  echo -e "  ${DIM}  • Serena             — semantic code navigation and editing${NC}"
  echo -e "  ${DIM}  • Sequential Thinking — structured multi-step reasoning${NC}"
  echo -e "  ${DIM}  • Context7           — up-to-date library documentation${NC}"
  echo
  echo -e "  ${CYAN}~/.claude/CLAUDE.md${NC} ${DIM}— persistent coding guidelines and preferences${NC}"
}

# ─── Step: Kiro agent configs ─────────────────────────────────────────────────
step_kiro_agents() {
  info "Downloading Kiro agent configs"

  local agents_dir="$HOME/.kiro/agents"
  mkdir -p "$agents_dir"

  local default_url="https://gist.githubusercontent.com/isaacgarza/ec26eec595ddc845c24f1c7d29994fea/raw/default.json"
  local cicd_url="https://gist.githubusercontent.com/isaacgarza/ec26eec595ddc845c24f1c7d29994fea/raw/ci-cd.json"

  # Detect the full uvx path; fall back to the bare name so PATH lookup happens at execution time
  local uvx_path
  uvx_path=$(command -v uvx 2>/dev/null || echo "uvx")

  _install_kiro_agent "$agents_dir/default.json" "$default_url" "$uvx_path" "default.json"
  _install_kiro_agent "$agents_dir/ci-cd.json" "$cicd_url" "$uvx_path" "ci-cd.json"
}

_install_kiro_agent() {
  local target=$1
  local url=$2
  local uvx_path=$3
  local label=$4

  local content
  content=$(curl -fsSL "$url") || { err "Failed to download $label"; return 1; }

  # Replace hardcoded Homebrew path with the detected uvx location
  content=$(echo "$content" | sed "s|/opt/homebrew/bin/uvx|${uvx_path}|g")

  if [[ -f "$target" ]]; then
    if confirm_n "$target already exists. Overwrite?"; then
      printf '%s\n' "$content" > "$target"
      success "Wrote $label"
    else
      skip
    fi
  else
    printf '%s\n' "$content" > "$target"
    success "Wrote $label"
  fi
}

# ─── Step runner ──────────────────────────────────────────────────────────────
run_steps() {
  local total=${#STEPS[@]}
  local index=1 ran=0 skipped=0
  local step name fn

  for step in "${STEPS[@]}"; do
    # Steps are stored as "display name|function name" — split on | to get each part
    name="${step%%|*}"
    fn="${step##*|}"

    echo -e "\n${DIM}[$index/$total]${NC} ${BOLD}$name${NC}"

    if confirm "  Run this step?"; then
      $fn
      ran=$(( ran + 1 ))
    else
      echo -e "  ${DIM}⊘ Skipped${NC}"
      skipped=$(( skipped + 1 ))
    fi

    index=$(( index + 1 ))
  done

  echo
  echo -e "${DIM}$ran run · $skipped skipped${NC}"
}

# ─── Main ─────────────────────────────────────────────────────────────────────
select_tools

# Guidelines step runs once and installs to all selected tools internally
register_step "Deploy AI coding guidelines" step_guidelines

if tool_selected "claude"; then
  register_step "MCP: Serena" step_mcp_serena
  register_step "MCP: Sequential Thinking" step_mcp_sequential_thinking
  register_step "MCP: Context7" step_mcp_context7
  register_step "Claude Code skills" step_claude_skills
  register_step "Claude Code agents" step_claude_agents
  register_step "Claude Code agent info" step_claude_agent_info
fi

if tool_selected "kiro"; then
  register_step "Kiro agent configs" step_kiro_agents
fi

run_steps

echo
echo -e "${BOLD}${GREEN}✓ AI tools setup complete!${NC}"

if tool_selected "claude"; then
  echo
  echo -e "  Verify MCPs: ${CYAN}claude mcp list${NC}"
fi
