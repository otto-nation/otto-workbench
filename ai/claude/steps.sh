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

# step_claude_rules — delegates to claude-rules sync which owns all rules logic.
step_claude_rules() {
  claude-rules sync
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
  install_cask "claude" "claude-code" "claude-code" "https://www.anthropic.com/claude-code"
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

# ─── Project scaffolding ─────────────────────────────────────────────────────
# These functions scaffold a per-project .claude/ directory. Called by
# `otto-workbench claude` when run from a project root.

# _detect_project_stacks — populates DETECTED_STACKS array with detected languages.
_detect_project_stacks() {
  DETECTED_STACKS=()

  if [[ -f "build.gradle.kts" ]]; then
    DETECTED_STACKS+=("kotlin")
  elif [[ -f "build.gradle" ]] && grep -q "kotlin" "build.gradle" 2>/dev/null; then
    DETECTED_STACKS+=("kotlin")
  elif [[ -f "build.gradle" || -f "pom.xml" ]]; then
    DETECTED_STACKS+=("java")
  fi

  [[ -f "go.mod" ]] && DETECTED_STACKS+=("go")

  if [[ -f "package.json" ]]; then
    if [[ -f "tsconfig.json" ]] || grep -q '"typescript"' "package.json" 2>/dev/null; then
      DETECTED_STACKS+=("typescript")
    else
      DETECTED_STACKS+=("node")
    fi
  fi

  if [[ -f "requirements.txt" || -f "pyproject.toml" || -f "setup.py" ]]; then
    DETECTED_STACKS+=("python")
  fi

  [[ -f "Cargo.toml" ]] && DETECTED_STACKS+=("rust")
  return 0
}

# _detect_build_commands — sets BUILD_CMD, TEST_CMD, RUN_CMD based on primary stack.
_detect_build_commands() {
  BUILD_CMD="" TEST_CMD="" RUN_CMD=""
  local primary="${DETECTED_STACKS[0]:-}"
  case "$primary" in
    kotlin|java)
      if [[ -f "gradlew" ]]; then
        BUILD_CMD="./gradlew build"; TEST_CMD="./gradlew test"
      elif [[ -f "pom.xml" ]]; then
        BUILD_CMD="mvn package"; TEST_CMD="mvn test"
      fi ;;
    go)
      BUILD_CMD="go build ./..."; TEST_CMD="go test ./..."; RUN_CMD="go run ." ;;
    typescript|node)
      local pm="npm"
      [[ -f "bun.lockb" || -f "bun.lock" ]] && pm="bun"
      [[ -f "pnpm-lock.yaml" ]] && pm="pnpm"
      [[ -f "yarn.lock" ]] && pm="yarn"
      BUILD_CMD="${pm} run build"; TEST_CMD="${pm} run test"; RUN_CMD="${pm} run dev" ;;
    python)
      TEST_CMD="pytest"; RUN_CMD="python -m <module>" ;;
    rust)
      BUILD_CMD="cargo build"; TEST_CMD="cargo test"; RUN_CMD="cargo run" ;;
  esac
}

# _build_stack_label — sets STACK_LABEL from DETECTED_STACKS.
_build_stack_label() {
  if [[ ${#DETECTED_STACKS[@]} -eq 0 ]]; then
    STACK_LABEL="(not detected)"; return
  fi
  local labels=() s
  for s in "${DETECTED_STACKS[@]}"; do
    case "$s" in
      kotlin) labels+=("Kotlin") ;; java) labels+=("Java") ;;
      go) labels+=("Go") ;; typescript) labels+=("TypeScript") ;;
      node) labels+=("Node.js") ;; python) labels+=("Python") ;;
      rust) labels+=("Rust") ;; *) labels+=("$s") ;;
    esac
  done
  local IFS=", "; STACK_LABEL="${labels[*]}"
}

# _scaffold_file SOURCE TARGET LABEL — copies source to target, skips if exists.
_scaffold_file() {
  local source="$1" target="$2" label="$3" force="${4:-false}"
  if [[ -f "$target" ]] && [[ "$force" == false ]]; then
    skip "$label (exists)"; return
  fi
  cp "$source" "$target"
  success "$label"
}

# _generate_claude_md TARGET — writes a lean project CLAUDE.md.
_generate_claude_md() {
  local target="$1" force="${2:-false}" project_name
  project_name="$(basename "$(pwd)")"
  if [[ -f "$target" ]] && [[ "$force" == false ]]; then
    skip "CLAUDE.md (exists)"; return
  fi

  local workflow=""
  [[ -n "$BUILD_CMD" ]] && workflow+="- Build: \`${BUILD_CMD}\`"$'\n'
  [[ -n "$TEST_CMD"  ]] && workflow+="- Test:  \`${TEST_CMD}\`"$'\n'
  [[ -n "$RUN_CMD"   ]] && workflow+="- Run:   \`${RUN_CMD}\`"$'\n'
  workflow="${workflow%$'\n'}"

  cat > "$target" <<EOF
# ${project_name}

<!-- TODO: Describe this project in 1-2 sentences -->

## Stack
${STACK_LABEL}

## Dev workflow
${workflow:-<!-- TODO: Add build/test/run commands -->}

## Key paths
<!-- TODO: Add paths Claude should know
     Examples:
       - Source:     src/main/kotlin/
       - Tests:      src/test/kotlin/
       - Config:     src/main/resources/
       - Generated:  build/generated/ (do not edit)
-->

## Notes
<!-- TODO: Add anything Claude should know before starting:
       - External dependencies (databases, services) required to run
       - Architectural constraints or patterns enforced in this repo
       - Anything that has burned you before
-->

## Rules
Project conventions load from \`.claude/rules/\` automatically.
Add personal rules as \`.claude/rules/<topic>.local.md\` (gitignored).
EOF
  success "CLAUDE.md"
}

# _scaffold_gitignore — creates .claude/rules/.gitignore and .claude/.gitignore.
_scaffold_gitignore() {
  local rules_gi=".claude/rules/.gitignore"
  if [[ ! -f "$rules_gi" ]]; then
    printf '*.local.md\n' > "$rules_gi"
  fi

  local claude_gi=".claude/.gitignore"
  if [[ ! -f "$claude_gi" ]]; then
    printf 'anatomy.md\n' > "$claude_gi"
  elif ! grep -qF 'anatomy.md' "$claude_gi" 2>/dev/null; then
    printf 'anatomy.md\n' >> "$claude_gi"
  fi
}

# scaffold_project_claude [--force] — scaffolds .claude/ in the current directory.
# Called by `otto-workbench claude` for project-level setup.
scaffold_project_claude() {
  local force=false
  [[ "${1:-}" == "--force" ]] && force=true

  _detect_project_stacks
  _detect_build_commands
  _build_stack_label

  if [[ ${#DETECTED_STACKS[@]} -gt 0 ]]; then
    info "Stack: ${STACK_LABEL}"
  else
    warn "No stack detected — generating base scaffold"
  fi
  echo

  mkdir -p .claude/rules

  info "Scaffolding .claude/"
  _generate_claude_md ".claude/CLAUDE.md" "$force"

  echo
  info "Scaffolding .claude/rules/"
  _scaffold_file "$CLAUDE_TEMPLATES_DIR/rules/conventions.md" ".claude/rules/conventions.md" "conventions.md" "$force"
  _scaffold_file "$CLAUDE_TEMPLATES_DIR/rules/testing.md"     ".claude/rules/testing.md"     "testing.md"     "$force"

  local s
  for s in "${DETECTED_STACKS[@]}"; do
    local tmpl="$CLAUDE_TEMPLATES_DIR/rules/${s}.md"
    [[ -f "$tmpl" ]] && _scaffold_file "$tmpl" ".claude/rules/${s}.md" "${s}.md" "$force"
  done

  _scaffold_gitignore
}

# ─── Summary ─────────────────────────────────────────────────────────────────

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
