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
  if jq --arg n "$name" 'del(.mcpServers[$n])' "$CLAUDE_CONFIG_FILE" > "$tmp"; then
    mv "$tmp" "$CLAUDE_CONFIG_FILE"
  fi
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
      [[ "${WORKBENCH_SYNC:-}" != true ]] && success "$name already registered" || true
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

  [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}$url${NC}" || true
  while IFS= read -r arg; do
    cmd_args+=("$arg")
  done < <(jq -r '.command[]' "$file")

  _mcp_install "$name" "${cmd_args[@]}"

  note=$(jq -r '.note // empty' "$file")
  if [[ -n "$note" && "${WORKBENCH_SYNC:-}" != true ]]; then echo -e "  ${DIM}$note${NC}"; fi
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
  if [[ ! -d "$CLAUDE_MCPS_SRC_DIR" ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && skip "No MCP configs in $CLAUDE_MCPS_SRC_DIR" || true
    return
  fi

  local file
  for file in "$CLAUDE_MCPS_SRC_DIR"/*.json; do
    [[ -e "$file" ]] || continue
    _mcp_install_from_manifest "$file"
  done
}

# step_claude_guidelines — copies CLAUDE.md into ~/.claude/.
# Supports user overrides: user/ai/claude/CLAUDE.md replaces the default,
# user/ai/claude/CLAUDE.local.md is appended after the default.
step_claude_guidelines() {
  [[ -f "$CLAUDE_GUIDELINES_SRC" ]] || { err "Missing $CLAUDE_GUIDELINES_SRC"; return 1; }
  mkdir -p "$CLAUDE_DIR"

  if [[ -f "$USER_GUIDELINES_SRC" ]]; then
    # Full replacement from user override
    install_file "$USER_GUIDELINES_SRC" "$CLAUDE_GUIDELINES_FILE" "CLAUDE.md (user override)"
  elif [[ -f "$USER_GUIDELINES_LOCAL" ]]; then
    # Append user additions to default
    local tmp
    tmp=$(mktemp)
    cat "$CLAUDE_GUIDELINES_SRC" "$USER_GUIDELINES_LOCAL" > "$tmp"
    install_file "$tmp" "$CLAUDE_GUIDELINES_FILE" "CLAUDE.md (+ user additions)"
    rm -f "$tmp"
  else
    install_file "$CLAUDE_GUIDELINES_SRC" "$CLAUDE_GUIDELINES_FILE"
  fi
}

# step_claude_rules — delegates to claude-rules sync which owns all rules logic.
step_claude_rules() {
  "$CLAUDE_SRC_DIR/bin/claude-rules" sync
}

# _claude_env_json VAR... — prints, as a JSON object, the values ~/.env.local
# sets for the named variables. Variables the file does not export are absent
# from the object rather than present and empty.
#
# The file is read directly instead of the ambient environment because the sync
# that matters most cannot see one: maintenance/bin/otto-workbench-maintenance
# runs `otto-workbench sync` from launchd with nothing but PATH set, so an
# environment-derived block would be blanked on every unattended run and restored
# by hand the next time someone synced from a terminal.
_claude_env_json() {
  local -a pairs=()
  local var line value
  for var in "$@"; do
    line=$(grep -m1 "^export ${var}=" "$ENV_LOCAL_FILE") || continue
    value=${line#*=}
    # A shell file quotes what needs quoting; settings.json carries the value.
    # Only a matched pair comes off — a lone quote is part of a line nothing here
    # can read confidently, and it travels intact rather than half-stripped.
    if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
      value=${value:1:${#value}-2}
    fi
    [[ -n "$value" ]] || continue
    pairs+=("$var"$'\t'"$value")
  done

  if [[ ${#pairs[@]} -eq 0 ]]; then
    printf '{}'
    return 0
  fi
  printf '%s\n' "${pairs[@]}" \
    | jq -Rn '[inputs | split("\t") | {key: .[0], value: .[1]}] | from_entries'
}

# _claude_mirror_env JSON — echoes the merged settings document with its `env`
# block reconciled against ~/.env.local, and echoes it back untouched when that
# file does not exist.
#
# The reconciliation is a mirror rather than a merge: a variable dropped from
# ~/.env.local is deleted here too, or a CLAUDE_CODE_USE_VERTEX left behind
# outlives the decision to stop routing through Vertex — settings.json wins over
# the environment in Claude Code, so a stale entry cannot be overridden from a
# shell. Only variables a registry declared with `meta.claude_env: true` are in
# scope; anything else under `.env` was put there by hand and stays.
_claude_mirror_env() {
  local result="$1"

  local -a managed=()
  collect_claude_env_vars managed "$WORKBENCH_STABLE_DIR"
  if [[ ! -f "$ENV_LOCAL_FILE" || ${#managed[@]} -eq 0 ]]; then
    printf '%s' "$result"
    return 0
  fi

  local env_json managed_json
  env_json=$(_claude_env_json "${managed[@]}")
  managed_json=$(printf '%s\n' "${managed[@]}" | jq -Rn '[inputs]')

  jq --argjson env "$env_json" --argjson managed "$managed_json" '
    .settings.env = (((.settings.env // {})
      | with_entries(select(.key | IN($managed[]) | not))) + $env)
    | if (.settings.env | length) == 0 then del(.settings.env) else . end
  ' <<< "$result"
}

# step_claude_settings — merges workbench settings.json template into the live
# settings file, preserving any existing user customisations.
# Supports user overrides: user/ai/claude/settings.json is deep-merged on top.
# The committed template carries handwritten permissions only; registry-derived
# ones are collected here, so the live file is where the two halves meet.
step_claude_settings() {
  mkdir -p "$CLAUDE_DIR"

  local existing="{}" content
  if [[ -f "$CLAUDE_SETTINGS_FILE" ]]; then
    content=$(cat "$CLAUDE_SETTINGS_FILE")
    if [[ -n "$content" ]]; then existing="$content"; fi
  fi

  local template
  template=$(cat "$CLAUDE_SETTINGS_SRC")

  # Merge user override settings into the template before applying
  if [[ -f "$USER_SETTINGS_SRC" ]]; then
    template=$(jq -n --argjson base "$template" --argjson user "$(cat "$USER_SETTINGS_SRC")" \
      '$base * $user')
  fi

  # Inject registry-derived permissions into the template
  # shellcheck source=/dev/null
  if ! declare -F collect_registry_permissions >/dev/null 2>&1; then
    . "$LIB_SRC_DIR/registries.sh"
  fi
  local -a registry_perms=()
  collect_registry_permissions registry_perms "$WORKBENCH_STABLE_DIR"
  if [[ ${#registry_perms[@]} -eq 0 ]]; then
    warn "No registry permissions collected — check registries under $WORKBENCH_STABLE_DIR"
  fi
  if [[ ${#registry_perms[@]} -gt 0 ]]; then
    local perms_json
    perms_json=$(printf '%s\n' "${registry_perms[@]}" | jq -Rn '[inputs]')
    template=$(jq --argjson rp "$perms_json" \
      '.permissions.allow = (.permissions.allow + $rp | unique)' <<< "$template")
  fi

  # Inject additionalDirectories — workbench-managed paths Claude needs access to
  local dirs_json
  # The config root is named separately now that it no longer sits inside the
  # state root — $HOME/.local covers the state root's new home, not ~/.config.
  dirs_json=$(jq -n \
    --arg claude "$CLAUDE_DIR" \
    --arg config "$WORKBENCH_CONFIG_DIR" \
    --arg state "$WORKBENCH_STATE_DIR" \
    --arg local "$HOME/.local" \
    '[$claude, $local, $config, $state] | unique')
  template=$(jq --argjson dirs "$dirs_json" \
    '.permissions.additionalDirectories = $dirs' <<< "$template")

  # The manifest of what the last sync wrote lives outside the settings file —
  # see CLAUDE_SETTINGS_MANIFEST in lib/constants.sh for why.
  local manifest="{}" manifest_content
  if [[ -f "$CLAUDE_SETTINGS_MANIFEST" ]]; then
    manifest_content=$(cat "$CLAUDE_SETTINGS_MANIFEST")
    if [[ -n "$manifest_content" ]]; then manifest="$manifest_content"; fi
  fi

  local result
  result=$(jq -n --argjson t "$template" --argjson e "$existing" --argjson m "$manifest" \
    -f "$CLAUDE_SYNC_SETTINGS_JQ") \
    || { err "Failed to sync settings.json"; return 1; }

  # After the merge, not through the template: sync-settings.jq adds a top-level
  # key only when the live file lacks one, so an env block routed that way would
  # be written once and never corrected again.
  result=$(_claude_mirror_env "$result") \
    || { err "Failed to mirror $ENV_LOCAL_FILE into settings.json"; return 1; }

  # Both halves are staged before either is published. The two files describe
  # each other: a manifest that ran ahead of the settings file — or trailed it —
  # reclassifies the difference as user entries the next sync must never touch,
  # so a template can lose an entry and the live file keep it forever. Staging
  # narrows the window a failed write leaves open to the pair of moves below.
  local settings_tmp manifest_tmp
  settings_tmp=$(mktemp)
  manifest_tmp=$(mktemp)
  if ! jq '.settings' <<< "$result" > "$settings_tmp" \
    || ! jq '.manifest' <<< "$result" > "$manifest_tmp"; then
    rm -f "$settings_tmp" "$manifest_tmp"
    err "Failed to render settings.json"
    return 1
  fi

  # mktemp answers 0600; the files these replace are world-readable config.
  chmod 644 "$settings_tmp" "$manifest_tmp"
  mkdir -p "$(dirname "$CLAUDE_SETTINGS_MANIFEST")"
  mv "$settings_tmp" "$CLAUDE_SETTINGS_FILE"
  mv "$manifest_tmp" "$CLAUDE_SETTINGS_MANIFEST"
  local label="settings.json synced"
  [[ "$existing" == "{}" ]] && label="settings.json written"
  [[ -f "$USER_SETTINGS_SRC" ]] && label+=" (+ user overrides)"
  [[ "${WORKBENCH_SYNC:-}" != true ]] && success "$label" || true
}

# step_claude_container_settings — copies each repo's tracked grants into the
# bare-repo container above its worktrees.
# Claude Code roots a project at the directory the session was launched in, and
# in a bare-repo layout that is the container, which holds no working tree — so
# a repo's tracked .claude/settings.json never loads and its own scripts prompt.
# Quiet unless something changed; lib/permission_mirror.py owns the decisions.
step_claude_container_settings() {
  python3 "$LIB_SRC_DIR/permission_mirror.py"
}

# step_claude_agents — copies each agent markdown file into ~/.claude/agents/.
# Supports user overrides: user/ai/claude/agents/<name>.md replaces the default,
# user/ai/claude/agents/<name>.disabled suppresses it entirely.
step_claude_agents() {
  [[ -d "$CLAUDE_AGENTS_SRC_DIR" ]] || { warn "No agents found in $CLAUDE_AGENTS_SRC_DIR — skipping"; return; }
  mkdir -p "$CLAUDE_AGENTS_DIR"
  [[ "${WORKBENCH_SYNC:-}" != true ]] && info "Installing Claude Code agents to $CLAUDE_AGENTS_DIR/" || true

  local -A layers
  resolve_layers "$CLAUDE_AGENTS_SRC_DIR" "$USER_AGENTS_DIR" "*.md" layers

  # Prune agents in target that are no longer in either layer
  local item
  for item in "$CLAUDE_AGENTS_DIR"/*.md; do
    [[ -e "$item" || -L "$item" ]] || continue
    local name
    name=$(basename "$item")
    if [[ -z "${layers[$name]+set}" ]]; then
      rm "$item"
      [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}⊘ pruned ${name%.md}${NC}" || true
    fi
  done

  # Install from merged layers
  local name source label
  for name in "${!layers[@]}"; do
    source="${layers[$name]}"
    label="${name%.md}"
    install_file "$source" "$CLAUDE_AGENTS_DIR/$name" "$label"
  done
}

# step_generate_tools — regenerates AI context markdown from registries and conventions.
step_generate_tools() {
  local tool_gen="$BIN_SRC_DIR/local/generate-tool-context"
  if [[ -x "$tool_gen" ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && info "Generating tool context" || true
    bash "$tool_gen"
  else
    warn "generate-tool-context not found — skipping tool context generation"
  fi

  local git_gen="$WORKBENCH_DIR/git/bin/local/generate-git-rules"
  if [[ -x "$git_gen" ]]; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && info "Generating git rules" || true
    bash "$git_gen"
  else
    warn "generate-git-rules not found — skipping git rules generation"
  fi
}

# step_claude_machine_profile — generates ~/.claude/machine/machine.md unconditionally.
# The generator has its own 24h staleness check; --force bypasses it for sync runs.
step_claude_machine_profile() {
  local generator="$CLAUDE_SKILLS_DIR/machine/generate-machine-profile.sh"
  if [[ ! -f "$generator" ]]; then
    warn "generate-machine-profile.sh not found — skipping"
    return
  fi
  [[ "${WORKBENCH_SYNC:-}" != true ]] && info "Generating machine profile" || true
  bash "$generator" --force
}

# step_claude_backup_memory — copies ~/.claude/projects/*/memory/*.md into ai/memory/.
# Preserves the slug directory structure so step_claude_restore_memory can reverse it.
step_claude_backup_memory() {
  local projects_dir="$CLAUDE_DIR/projects"
  local backup_dir="$AI_MEMORY_BACKUP_DIR"
  [[ -d "$projects_dir" ]] || { skip "No ~/.claude/projects/ — skipping memory backup"; return; }
  mkdir -p "$backup_dir"

  local slug mem_dir dest count=0
  for mem_dir in "$projects_dir"/*/memory/; do
    [[ -d "$mem_dir" ]] || continue
    slug=$(basename "$(dirname "$mem_dir")")
    dest="$backup_dir/$slug"
    mkdir -p "$dest"
    local f
    for f in "$mem_dir"*.md; do
      [[ -f "$f" ]] || continue
      cp "$f" "$dest/"
      (( count++ )) || true
    done
  done
  [[ "${WORKBENCH_SYNC:-}" != true ]] && success "Memory backed up ($count files → ai/memory/)" || true
}

# step_claude_restore_memory — copies ai/memory/ back to ~/.claude/projects/*/memory/.
# Only runs when a project memory directory is absent (new-machine setup guard).
step_claude_restore_memory() {
  local backup_dir="$AI_MEMORY_BACKUP_DIR"
  [[ -d "$backup_dir" ]] || { skip "No ai/memory/ backup — skipping restore"; return; }

  local slug dest_base count=0
  for slug_dir in "$backup_dir"/*/; do
    [[ -d "$slug_dir" ]] || continue
    slug=$(basename "$slug_dir")
    dest_base="$CLAUDE_DIR/projects/$slug/memory"
    # Only restore if memory dir is absent or empty — never overwrite existing session learning
    if [[ -d "$dest_base" ]] && [[ -n "$(ls -A "$dest_base" 2>/dev/null)" ]]; then
      skip "Memory for $slug already exists — skipping restore"
      continue
    fi
    mkdir -p "$dest_base"
    local f
    for f in "$slug_dir"*.md; do
      [[ -f "$f" ]] || continue
      cp "$f" "$dest_base/"
      (( count++ )) || true
    done
  done
  if [[ $count -gt 0 ]]; then
    success "Memory restored ($count files ← ai/memory/)"
  else
    skip "Nothing to restore"
  fi
}

# step_install_claude — installs Claude Code via its own installer if not
# already in PATH.
#
# The native installer rather than the Homebrew cask. The cask's artifact is a
# bare Mach-O binary, and brew stamps com.apple.quarantine on what it downloads;
# a notarization ticket cannot be stapled to a bare Mach-O, so Gatekeeper has to
# look one up online at exec time and refuses the launch outright — "Apple could
# not verify claude is free of malware", with Move to Trash as the only offer.
# The installer's curl download carries no quarantine attribute, so the question
# is never asked. It also self-updates, which the cask does not.
step_install_claude() {
  install_via_installer claude "$CLAUDE_INSTALL_URL" "Claude Code"
}

# step_claude_worktrunk_plugin — installs Worktrunk's Claude Code plugin for
# worktree-isolated agent sessions. One-time interactive setup; skips if wt is
# not installed or the plugin is already present.
step_claude_worktrunk_plugin() {
  command -v wt >/dev/null 2>&1 || {
    warn "worktrunk not installed — brew install worktrunk, then re-run: otto-workbench sync ai"
    return
  }

  if wt config plugins list 2>/dev/null | grep -q "claude"; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "Worktrunk Claude plugin already installed" || true
    return
  fi

  info "Installing Worktrunk Claude Code plugin"
  wt config plugins claude install
  success "Worktrunk Claude plugin installed"
}

register_claude_steps() {
  register_step "Install claude-code"     step_install_claude
  register_step "Tool context"            step_generate_tools
  register_step "Claude Code settings"    step_claude_settings
  register_step "Container grants"        step_claude_container_settings
  register_step "Claude Code guidelines"  step_claude_guidelines
  register_step "Claude Code rules"       step_claude_rules
  register_step "MCP servers"             step_claude_mcps
  register_step "Claude Code agents"      step_claude_agents
  register_step "Worktrunk Claude plugin" step_claude_worktrunk_plugin
}

# _profile_excludes_skill PROFILE SKILL — returns 0 if the profile excludes the skill.
_profile_excludes_skill() {
  local profile="$1" skill="$2"
  local profiles_file="$AI_SRC_DIR/profiles.yml"
  [[ -f "$profiles_file" ]] || return 1
  yq -e ".profiles.${profile}.exclude.skills[] | select(. == \"${skill}\")" "$profiles_file" >/dev/null 2>&1
}

# _export_claude_config DIR PROFILE — copies Claude configs into DIR, filtered by profile.
# Produces a self-contained directory suitable for deployment to servers/containers.
# Only copies settings, CLAUDE.md, rules, agents, and skills (filtered by profile).
# MCPs, machine profile, memory, plugins, and scripts are intentionally excluded.
_export_claude_config() {
  local dest="$1" profile="${2:-server}"

  mkdir -p "$dest/rules" "$dest/agents" "$dest/skills"

  # Settings: copy base template without user overrides or registry permissions.
  # Registry permissions are a sync-time injection against the local registries,
  # so an exported session carries the handwritten allow list only and prompts
  # for registry tools. Deriving them here would ship the exporting machine's
  # tool set to a host that may not have it installed.
  if [[ -f "$CLAUDE_SETTINGS_SRC" ]]; then
    cp "$CLAUDE_SETTINGS_SRC" "$dest/settings.json"
  fi

  # CLAUDE.md: copy base without user overrides
  if [[ -f "$CLAUDE_GUIDELINES_SRC" ]]; then
    cp "$CLAUDE_GUIDELINES_SRC" "$dest/CLAUDE.md"
  fi

  # Rules: copy all .md files from guidelines/rules
  local rule
  for rule in "$GUIDELINES_RULES_SRC_DIR"/*.md; do
    [[ -f "$rule" ]] || continue
    cp "$rule" "$dest/rules/"
  done

  # Agents: copy all .md files
  local agent
  for agent in "$CLAUDE_AGENTS_SRC_DIR"/*.md; do
    [[ -f "$agent" ]] || continue
    cp "$agent" "$dest/agents/"
  done

  # Skills: copy directories, filtered by profile
  #
  # An agent-backed skill is dropped before the profile is consulted, because the
  # reason it cannot ship is structural rather than a preference any profile could
  # hold. What is on disk for one is a frontmatter block over an unresolved
  # AGENT_PROTOCOL_PLACEHOLDER — ai/skills/steps.sh splices the protocol in at
  # install time, and this export is a verbatim copy. Shipped, it would land in the
  # target host's ~/.claude/skills/ as a skill with no instructions in it, and the
  # protocol it is missing is already in this same export as agents/<name>.md.
  local skill skill_name
  for skill in "$SKILLS_SRC_DIR"/*/; do
    [[ -d "$skill" ]] || continue
    skill_name=$(basename "$skill")
    if [[ -n "$(skill_agent "$skill/SKILL.md")" ]]; then
      continue
    fi
    if _profile_excludes_skill "$profile" "$skill_name"; then
      continue
    fi
    cp -R "$skill" "$dest/skills/$skill_name"
  done
}

# sync_claude — runs all Claude sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
# Skips silently if claude is not installed on this machine.
#
# Flags (used by workbench-export, not by otto-workbench sync):
#   --export DIR    Write configs to DIR instead of ~/.claude/
#   --profile NAME  Filter components by profile (default: server)
sync_claude() {
  local export_dir="" profile=""
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --export)  export_dir="$2"; shift 2 ;;
      --profile) profile="$2"; shift 2 ;;
      *)         shift ;;
    esac
  done

  if [[ -n "$export_dir" ]]; then
    _export_claude_config "$export_dir" "${profile:-server}"
    return
  fi

  command -v claude >/dev/null 2>&1 || { warn "claude not found in PATH — skipping"; return; }

  sync_header "claude scripts → $LOCAL_BIN_DIR/"
  sync_component_bin "$CLAUDE_SRC_DIR"

  sync_header "Claude settings"
  step_claude_settings
  step_claude_container_settings

  sync_header "Claude guidelines + rules"
  step_claude_guidelines
  step_claude_rules

  sync_header "Claude MCPs"
  step_claude_mcps

  sync_header "Claude agents"
  step_claude_agents

  sync_header "Machine profile"
  step_claude_machine_profile

  sync_header "Memory backup"
  step_claude_backup_memory
}

# ─── Project scaffolding ─────────────────────────────────────────────────────
# These functions scaffold a per-project .claude/ directory. Called by
# `otto-workbench ai init` when run from a project root.

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
  [[ -d "ansible" ]] && DETECTED_STACKS+=("ansible")
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
      rust) labels+=("Rust") ;; ansible) labels+=("Ansible") ;; *) labels+=("$s") ;;
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

## Stack
${STACK_LABEL}

## Dev workflow
${workflow}

## Key paths

## Notes

## Rules
Project conventions load from \`.claude/rules/\` automatically in Claude Code.
Add personal rules as \`.claude/rules/<topic>.local.md\` (gitignored).

This file lives at the repository root so every agent harness reads it — Pi
resolves a context file per directory and never looks inside \`.claude/\`.
EOF
  success "CLAUDE.md"
}

# Everything a project's .claude/ holds that belongs to one machine rather than
# the repo: regenerated context and Claude Code's own per-machine grants. The
# rest of the directory — CLAUDE.md, rules/, settings.json — is committed, so
# these are excluded by name. This repo's own .claude/.gitignore is held to the
# same list by tests/claude_settings.bats.
CLAUDE_LOCAL_ARTIFACTS=(anatomy.md ceiling-debt.md settings.local.json)

# _scaffold_gitignore — creates .claude/rules/.gitignore and .claude/.gitignore.
_scaffold_gitignore() {
  local rules_gi=".claude/rules/.gitignore"
  if [[ ! -f "$rules_gi" ]]; then
    printf '*.local.md\n' > "$rules_gi"
  fi

  local review_gi=".claude/review/.gitignore"
  if [[ ! -f "$review_gi" ]]; then
    printf '*.local.md\n' > "$review_gi"
  fi

  # Appended one name at a time rather than rewritten: the file may already
  # carry entries this repo knows nothing about, and a project that predates a
  # new artifact still has to pick it up.
  local claude_gi=".claude/.gitignore" artifact
  touch "$claude_gi"
  for artifact in "${CLAUDE_LOCAL_ARTIFACTS[@]}"; do
    grep -qxF "$artifact" "$claude_gi" || printf '%s\n' "$artifact" >> "$claude_gi"
  done
}

# scaffold_project_claude [--force] — scaffolds .claude/ and the root CLAUDE.md
# in the current directory.
# Called by `otto-workbench ai init` for project-level setup.
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

  info "Scaffolding CLAUDE.md"
  _generate_claude_md "CLAUDE.md" "$force"

  echo
  mkdir -p .claude/rules .claude/review
  info "Scaffolding .claude/rules/"
  _scaffold_file "$CLAUDE_TEMPLATES_DIR/rules/conventions.md" ".claude/rules/conventions.md" "conventions.md" "$force"
  _scaffold_file "$CLAUDE_TEMPLATES_DIR/rules/testing.md"     ".claude/rules/testing.md"     "testing.md"     "$force"

  local s
  for s in "${DETECTED_STACKS[@]}"; do
    local tmpl="$CLAUDE_TEMPLATES_DIR/rules/${s}.md"
    if [[ -f "$tmpl" ]]; then _scaffold_file "$tmpl" ".claude/rules/${s}.md" "${s}.md" "$force"; fi
  done

  # Scaffold architecture.md for stacks that benefit from architecture narrative
  for s in "${DETECTED_STACKS[@]}"; do
    local arch_tmpl="$CLAUDE_TEMPLATES_DIR/architecture/${s}.md"
    if [[ -f "$arch_tmpl" ]]; then
      echo
      info "Scaffolding .claude/architecture.md"
      _scaffold_file "$arch_tmpl" ".claude/architecture.md" "architecture.md" "$force"
      break
    fi
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

  _print_override_summary
}

# _print_override_summary — lists active user overrides from user/ai/.
_print_override_summary() {
  [[ -d "$USER_AI_DIR" ]] || return 0

  local found=false
  local item

  # Check override files
  for item in "$USER_GUIDELINES_SRC" "$USER_GUIDELINES_LOCAL" "$USER_SETTINGS_SRC"; do
    [[ -f "$item" ]] || continue
    if [[ "$found" == false ]]; then
      echo
      echo -e "  ${CYAN}User overrides${NC} ${DIM}(user/ai/)${NC}"
      found=true
    fi
    echo -e "  ${DIM}  • $(basename "$item")${NC}"
  done

  # Check override directories
  local dir label
  for dir in "$USER_AGENTS_DIR:agents" "$USER_SKILLS_DIR:skills" "$USER_RULES_DIR:rules"; do
    label="${dir##*:}"
    dir="${dir%%:*}"
    [[ -d "$dir" ]] || continue
    for item in "$dir"/*; do
      [[ -e "$item" ]] || continue
      [[ "$found" == false ]] && { echo; echo -e "  ${CYAN}User overrides${NC} ${DIM}(user/ai/)${NC}"; found=true; }
      echo -e "  ${DIM}  • $label/$(basename "$item")${NC}"
    done
  done
}
