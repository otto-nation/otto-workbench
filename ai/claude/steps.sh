#!/usr/bin/env bash
# Claude Code setup steps — sourced by ai/setup.sh and bin/otto-workbench.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# ─── Helpers ──────────────────────────────────────────────────────────────────

# _mcp_is_registered NAME — returns 0 if NAME is already in ~/.claude.json mcpServers.
# Reads the config file directly to avoid `claude mcp list`, which probes all servers
# (triggering health checks that start MCP processes like Serena).
_mcp_is_registered() {
  jq -e ".mcpServers | has(\"$1\")" "$CLAUDE_CONFIG_FILE" > /dev/null 2>&1
}

# _mcp_registered_cmd NAME — prints the registered command + args joined by spaces.
_mcp_registered_cmd() {
  jq -r --arg n "$1" \
    '[.mcpServers[$n].command] + (.mcpServers[$n].args // []) | join(" ")' \
    "$CLAUDE_CONFIG_FILE" 2>/dev/null
}

# _mcp_update NAME — removes the existing registration so it can be re-added with
# a new command. Used when drift is detected between registered and expected command.
_mcp_update() {
  local name="$1"
  info "Updating $name (command changed)"
  local tmp
  tmp=$(mktemp)
  # shellcheck disable=SC2064  # $tmp must expand now to capture this invocation's value
  trap "rm -f '$tmp'" RETURN
  jq --arg n "$name" 'del(.mcpServers[$n])' "$CLAUDE_CONFIG_FILE" > "$tmp" \
    && mv "$tmp" "$CLAUDE_CONFIG_FILE"
}

# _mcp_install NAME COMMAND... — registers an MCP server at user scope.
# Skips if already registered with the same command. Updates if the command has drifted
# (e.g. a hardcoded API key was previously baked in but has since been removed from the manifest).
_mcp_install() {
  local name="$1"; shift
  local expected="$*"

  if _mcp_is_registered "$name"; then
    local registered
    registered=$(_mcp_registered_cmd "$name")
    if [[ "$registered" == "$expected" ]]; then
      success "$name already registered"
      return
    fi
    _mcp_update "$name"
  else
    info "Installing $name (user scope)"
  fi

  claude mcp add "$name" --scope user -- "$@"
  success "$name registered"
}

# _mcp_install_from_manifest FILE — reads an MCP manifest JSON and calls _mcp_install.
# Manifest fields: url (required, display only), command[] (required), note (optional).
_mcp_install_from_manifest() {
  local file="$1"
  local name url note
  local cmd_args=()

  name=$(basename "$file" .json)
  url=$(jq -r '.url // empty' "$file")
  [[ -z "$url" ]] && { err "$name: manifest is missing required field: url"; return 1; }

  echo -e "  ${DIM}$url${NC}"
  while IFS= read -r arg; do
    cmd_args+=("$arg")
  done < <(jq -r '.command[]' "$file")

  _mcp_install "$name" "${cmd_args[@]}"

  note=$(jq -r '.note // empty' "$file")
  if [[ -n "$note" ]]; then echo -e "  ${DIM}$note${NC}"; fi
}

# _install_workbench_rule — writes the workbench.md rule file with current paths baked in.
_install_workbench_rule() {
  local target="$CLAUDE_RULES_DIR/workbench.md"

  mkdir -p "$CLAUDE_RULES_DIR"
  cat > "$target" <<EOF
# Workbench

Your developer environment is managed at: $WORKBENCH_DIR

What it owns: Claude config (\`ai/claude/\`), coding rules (\`ai/guidelines/rules/\`),
bin scripts (\`bin/\`), zsh config (\`zsh/\`), git config (\`git/\`),
Docker setup (\`docker/\`), Brew packages (\`brew/\`).

When modifying Claude config (MCPs, agents, skills, settings), bin scripts, zsh config,
git config, or developer tooling — make the change in the workbench repo and re-run
the relevant setup script. Do not edit \`~/\` directly.

Re-run AI setup:   bash $AI_SRC_DIR/setup.sh
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
    echo -e "  ${DIM}  Run 'claude-rules status' to review, or edit $GUIDELINES_RULES_SRC_DIR/ to formalize${NC}"
  fi
}

# _print_item_list LABEL DIR GLOB — prints a cyan section header followed by a
# bulleted list of matching items. Prints "(none)" if no items are found.
_print_item_list() {
  local label="$1" dir="$2" glob="$3"
  local found=false item
  echo -e "  ${CYAN}${label}${NC}"
  for item in "$dir"/$glob; do
    [[ -e "$item" ]] || continue
    local name
    name=$(basename "$item")
    name="${name%.md}"
    echo -e "  ${DIM}  • $name${NC}"
    found=true
  done
  [[ "$found" == false ]] && echo -e "  ${DIM}  (none)${NC}"
  echo
}

# ─── Steps ────────────────────────────────────────────────────────────────────

# step_claude_mcps — installs all MCP servers discovered from ai/claude/mcps/*.json.
step_claude_mcps() {
  [[ -d "$CLAUDE_MCPS_SRC_DIR" ]] || { warn "No MCP configs found in $CLAUDE_MCPS_SRC_DIR — skipping"; return; }

  local file
  for file in "$CLAUDE_MCPS_SRC_DIR"/*.json; do
    [[ -e "$file" ]] || continue
    _mcp_install_from_manifest "$file"
  done
}

# step_claude_guidelines — copies CLAUDE.md into ~/.claude/.
step_claude_guidelines() {
  [[ -f "$CLAUDE_GUIDELINES_SRC" ]] || { err "Missing $CLAUDE_GUIDELINES_SRC"; return 1; }
  mkdir -p "$CLAUDE_DIR"
  install_file "$CLAUDE_GUIDELINES_SRC" "$CLAUDE_GUIDELINES_FILE"
}

# step_claude_rules — copies workbench rules into ~/.claude/rules/ and
# generates workbench.md with current repo paths baked in.
step_claude_rules() {
  [[ -d "$GUIDELINES_RULES_SRC_DIR" ]] || { warn "No rules found in $GUIDELINES_RULES_SRC_DIR — skipping"; return; }

  mkdir -p "$CLAUDE_RULES_DIR"
  info "Installing rules to $CLAUDE_RULES_DIR/"
  copy_dir "$GUIDELINES_RULES_SRC_DIR" "$CLAUDE_RULES_DIR" "$RULES_GLOB" --strip-ext --prune

  echo
  info "Generating workbench.md"
  _install_workbench_rule

  _check_local_rules "$CLAUDE_RULES_DIR"
}

# step_claude_settings — merges workbench settings.json template into the live
# settings file, preserving any existing user customisations.
step_claude_settings() {
  mkdir -p "$CLAUDE_DIR"

  local existing="{}" content
  if [[ -f "$CLAUDE_SETTINGS_FILE" ]]; then
    content=$(cat "$CLAUDE_SETTINGS_FILE")
    [[ -n "$content" ]] && existing="$content"
  fi

  local result
  result=$(jq -n --argjson t "$(cat "$CLAUDE_SETTINGS_SRC")" --argjson e "$existing" -f "$CLAUDE_SYNC_SETTINGS_JQ") \
    || { err "Failed to sync settings.json"; return 1; }

  printf '%s\n' "$result" > "$CLAUDE_SETTINGS_FILE"
  if [[ "$existing" == "{}" ]]; then success "settings.json written"; else success "settings.json synced"; fi
}

# step_claude_skills — symlinks each skill directory into ~/.claude/skills/.
step_claude_skills() {
  [[ -d "$CLAUDE_SKILLS_SRC_DIR" ]] || { warn "No skills found in $CLAUDE_SKILLS_SRC_DIR — skipping"; return; }
  mkdir -p "$CLAUDE_SKILLS_DIR"
  info "Installing Claude Code skills to $CLAUDE_SKILLS_DIR/"
  symlink_dir "$CLAUDE_SKILLS_SRC_DIR" "$CLAUDE_SKILLS_DIR" "*/"
}

# step_claude_agents — copies each agent markdown file into ~/.claude/agents/.
step_claude_agents() {
  [[ -d "$CLAUDE_AGENTS_SRC_DIR" ]] || { warn "No agents found in $CLAUDE_AGENTS_SRC_DIR — skipping"; return; }
  mkdir -p "$CLAUDE_AGENTS_DIR"
  info "Installing Claude Code agents to $CLAUDE_AGENTS_DIR/"
  copy_dir "$CLAUDE_AGENTS_SRC_DIR" "$CLAUDE_AGENTS_DIR" "*.md" --strip-ext --prune
}

# step_generate_tools — regenerates the AI tool context markdown from registries.
step_generate_tools() {
  local generator="$BIN_SRC_DIR/generate-tool-context"
  if [[ ! -x "$generator" ]]; then
    warn "generate-tool-context not found — skipping tool context generation"
    return
  fi
  info "Generating tool context"
  bash "$generator"
}

# step_install_claude — installs claude-code via brew if not already in PATH.
step_install_claude() {
  _ai_install_cask "claude" "claude-code" "claude-code" "https://www.anthropic.com/claude-code"
}

register_claude_steps() {
  register_step "Install claude-code"     step_install_claude
  register_step "Tool context"            step_generate_tools
  register_step "Claude Code settings"    step_claude_settings
  register_step "Claude Code guidelines"  step_claude_guidelines
  register_step "Claude Code rules"       step_claude_rules
  register_step "MCP servers"             step_claude_mcps
  register_step "Claude Code skills"      step_claude_skills
  register_step "Claude Code agents"      step_claude_agents
}

# sync_claude — runs all Claude sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
# Skips silently if claude is not installed on this machine.
sync_claude() {
  command -v claude >/dev/null 2>&1 || { warn "claude not found in PATH — skipping"; return; }

  echo; info "Claude settings"
  step_claude_settings

  echo; info "Claude guidelines + rules"
  step_claude_guidelines
  step_claude_rules

  echo; info "Claude MCPs"
  step_claude_mcps

  echo; info "Claude skills + agents"
  step_claude_skills
  step_claude_agents
}

print_claude_summary() {
  echo
  info "Claude Code"
  echo

  if [[ -f "$CLAUDE_CONFIG_FILE" ]]; then
    echo -e "  ${CYAN}MCP servers${NC}"
    local found=false mcp_name
    while IFS= read -r mcp_name; do
      echo -e "  ${DIM}  • $mcp_name${NC}"
      found=true
    done < <(jq -r '.mcpServers | keys[]' "$CLAUDE_CONFIG_FILE" 2>/dev/null)
    [[ "$found" == false ]] && echo -e "  ${DIM}  (none)${NC}"
    echo
  fi

  _print_item_list "Skills"  "$CLAUDE_SKILLS_DIR"  "*/"
  _print_item_list "Agents"  "$CLAUDE_AGENTS_DIR"  "*.md"
  _print_item_list "Rules"   "$CLAUDE_RULES_DIR"   "*.md"

  echo -e "  ${DIM}  $CLAUDE_GUIDELINES_FILE   — persistent guidelines${NC}"
  echo -e "  ${DIM}  $CLAUDE_SETTINGS_FILE     — persistent permissions${NC}"
}
