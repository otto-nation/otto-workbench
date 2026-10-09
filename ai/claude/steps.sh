#!/usr/bin/env bash
# Claude Code setup steps — sourced by ai/setup.sh and bin/otto-workbench.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Project scaffolding lives beside this file in scaffold.sh. Sourcing it here keeps
# steps.sh the one file component discovery has to find. Relative to this file
# rather than $CLAUDE_SRC_DIR, so a copied tree loads its own copy.
# shellcheck source=scaffold.sh
. "$(dirname "${BASH_SOURCE[0]}")/scaffold.sh"

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

# step_claude_rules — installs the rules that reach Claude Code as symlinks in
# ~/.claude/rules/.
#
# The merged set comes from resolve_rules, which every harness shares. This step
# owns only the half that is Claude Code's: which of those rules it is scoped to
# load, and the directory layout it wants them in. workbench-rules refreshes the
# layers resolve_rules reads and installs for nobody.
step_claude_rules() {
  local -A layers
  resolve_rules layers

  # Drop the rules this harness is scoped out of before the prune loop sees the
  # map. Removing them here rather than at install time is what makes a rule
  # that *becomes* Pi-only get pruned from ~/.claude/rules/ on the next sync,
  # instead of lingering as a symlink nothing will ever remove.
  local rule
  for rule in "${!layers[@]}"; do
    rule_harness_ok "${layers[$rule]}" claude || unset "layers[$rule]"
  done

  mkdir -p "$CLAUDE_RULES_DIR"

  # Prune the links this step is responsible for — one pointing into a layer
  # root — and leave anything else an operator put here alone.
  local -a roots=()
  while IFS= read -r rule; do roots+=("$rule"); done < <(rules_layer_roots)

  local item target name root owned
  for item in "$CLAUDE_RULES_DIR"/$RULES_GLOB; do
    [[ -L "$item" ]] || continue
    target=$(readlink "$item")
    owned=false
    for root in "${roots[@]}"; do
      if [[ "$target" == "$root"/* ]]; then owned=true; break; fi
    done
    [[ "$owned" == true ]] || continue
    name=$(basename "$item")
    if [[ -z "${layers[$name]+set}" ]]; then
      rm "$item"
      echo -e "  ${DIM}⊘ pruned ${name%.md}${NC}"
    fi
  done

  # Replace old copies with symlinks where a source exists
  for item in "$CLAUDE_RULES_DIR"/$RULES_GLOB; do
    [[ -f "$item" && ! -L "$item" ]] || continue
    name=$(basename "$item")
    if [[ -n "${layers[$name]+set}" ]]; then rm "$item"; fi
  done

  for name in "${!layers[@]}"; do
    install_symlink "${layers[$name]}" "$CLAUDE_RULES_DIR/$name" "${name%.md}"
  done
}

# _claude_env_json ENTRY... — prints, as a JSON object, the values ~/.env.local
# sets for the named variables. Each ENTRY is either a bare variable name (read
# and output under the same key) or a tab-separated "source\ttarget" pair (read
# source from ~/.env.local, output under target key). Variables the file does
# not export are absent from the object rather than present and empty.
#
# Reading goes through read_env_local_var (lib/env.sh) rather than the ambient
# environment because the sync that matters most cannot see one:
# maintenance/bin/otto-workbench-maintenance runs `otto-workbench sync` from
# launchd with nothing but PATH set, so an environment-derived block would be
# blanked on every unattended run and restored by hand the next time someone
# synced from a terminal.
_claude_env_json() {
  local -a pairs=()
  local entry source key value
  for entry in "$@"; do
    if [[ "$entry" == *$'\t'* ]]; then
      source="${entry%%$'\t'*}"
      key="${entry#*$'\t'}"
    else
      source="$entry"
      key="$entry"
    fi
    value=$(read_env_local_var "$source")
    [[ -n "$value" ]] || continue
    pairs+=("$key"$'\t'"$value")
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
#
# The one exception is an all-empty read, which is treated as a failure to read
# rather than a wholesale withdrawal — see the guard below.
_claude_mirror_env() {
  local result="$1"

  # The file is checked before the registry scan, not after it: with no
  # ~/.env.local there is nothing to mirror whatever the registries declare, and
  # the scan is the expensive half — a full load of every registry, which under
  # bats' per-command DEBUG trap took sync_idempotency.bats minutes per call.
  if [[ ! -f "$ENV_LOCAL_FILE" ]]; then
    printf '%s' "$result"
    return 0
  fi

  local -a sources=() targets=()
  collect_claude_env_vars sources targets "$WORKBENCH_STABLE_DIR"
  if [[ ${#sources[@]} -eq 0 ]]; then
    printf '%s' "$result"
    return 0
  fi

  # Build "source\ttarget" entries for _claude_env_json so it reads from
  # ~/.env.local using the source name and outputs under the target name.
  local -a entries=()
  local i
  for (( i=0; i<${#sources[@]}; i++ )); do
    entries+=("${sources[i]}"$'\t'"${targets[i]}")
  done

  local env_json managed_json
  env_json=$(_claude_env_json "${entries[@]}")

  # Not one declared variable resolved, across every flagged registry. Far more
  # often a ~/.env.local this run could not read as expected than a machine
  # turning off every harness variable at once, and withdrawal is the direction
  # that cannot be undone from a shell. Emptying the block stays available by
  # dropping the registry flag.
  #
  # A backstop, not the guard against a partial read: a rename that leaves some
  # names resolving still withdraws the rest, which is why every entry point
  # runs migrations before reaching here.
  if [[ "$env_json" == '{}' ]]; then
    printf '%s' "$result"
    return 0
  fi

  # The managed list uses target names — those are the keys that appear in the
  # env block and need to be tracked for cleanup when dropped from ~/.env.local.
  managed_json=$(printf '%s\n' "${targets[@]}" | jq -Rn '[inputs]')

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
  # One registry scan for the whole step. Two collectors run below —
  # collect_registry_permissions here and collect_claude_env_vars inside
  # _claude_mirror_env — and each re-scans on its own, so the yq parse of every
  # registry ran twice per sync and the second threw away what the first had
  # cached. Safe because the step only reads the registry tree; see
  # `reg_scan_hold` in lib/registries.sh for the bound that makes it so.
  if ! declare -F reg_scan_hold >/dev/null 2>&1; then
    # shellcheck source=/dev/null
    . "$LIB_SRC_DIR/registries.sh"
  fi
  reg_scan_hold _step_claude_settings
}

_step_claude_settings() {
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

  # Inject registry-derived permissions into the template. No source guard
  # here: step_claude_settings above sources the module before opening the
  # scan hold, so by this line the collectors are always defined.
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

# step_claude_backup_memory — copies $WORKBENCH_MEMORY_DIR/*/*.md into ai/memory/.
# Preserves the repo-key directory structure so step_claude_restore_memory can
# reverse it.
step_claude_backup_memory() {
  local projects_dir="$WORKBENCH_MEMORY_DIR"
  local backup_dir="$AI_MEMORY_BACKUP_DIR"
  [[ -d "$projects_dir" ]] || { skip "No memory store — skipping memory backup"; return; }
  mkdir -p "$backup_dir"

  local slug mem_dir dest count=0
  for mem_dir in "$projects_dir"/*/; do
    [[ -d "$mem_dir" ]] || continue
    slug=$(basename "$mem_dir")
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

# step_claude_restore_memory — copies ai/memory/ back to $WORKBENCH_MEMORY_DIR/*/.
# Only runs when a repo's memory directory is absent (new-machine setup guard).
step_claude_restore_memory() {
  local backup_dir="$AI_MEMORY_BACKUP_DIR"
  [[ -d "$backup_dir" ]] || { skip "No ai/memory/ backup — skipping restore"; return; }

  local slug dest_base count=0
  for slug_dir in "$backup_dir"/*/; do
    [[ -d "$slug_dir" ]] || continue
    slug=$(basename "$slug_dir")
    # Not every directory under ai/memory/ is a repo key. The retro archive
    # (ai/memory/retro/) and the machine profile backup (ai/memory/machine/)
    # live here too, and a repo key is always a path-derived name starting
    # with '-'. Restoring a non-key directory would invent a memory directory
    # and fill it with files that are not any repo's memory.
    [[ "$slug" == -* ]] || continue
    dest_base="$WORKBENCH_MEMORY_DIR/$slug"
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
# worktree-isolated agent sessions, installing worktrunk itself first (Homebrew,
# or mise on a machine without it) when wt is missing. Skips when worktrunk
# cannot be installed or the plugin is already present.
step_claude_worktrunk_plugin() {
  install_brew_or_mise wt worktrunk worktrunk worktrunk || {
    warn "Worktrunk Claude plugin skipped — re-run after installing worktrunk: otto-workbench sync ai"
    return 0
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
  register_step "Claude Code settings"    step_claude_settings
  register_step "Container grants"        step_claude_container_settings
  register_step "Claude Code guidelines"  step_claude_guidelines
  register_step "Claude Code rules"       step_claude_rules
  register_step "MCP servers"             step_claude_mcps
  register_step "Claude Code agents"      step_claude_agents
  register_step "Install worktrunk + Claude plugin" step_claude_worktrunk_plugin
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

# step_claude_version — warns when the installed Claude Code predates
# CLAUDE_CODE_MIN_VERSION, the first release that reads a repo's AGENTS.md as
# its project instructions. otto-workbench writes AGENTS.md and expects that
# release; an older one silently loads no project instructions at all. Warns
# rather than updating: `claude update` is the operator's to run.
step_claude_version() {
  local have
  have="$(claude --version 2>/dev/null | sed -n '1s/^\([0-9][0-9.]*\).*/\1/p')" || have=""
  [[ -n "$have" ]] || return 0
  if version_at_least "$have" "$CLAUDE_CODE_MIN_VERSION"; then
    return 0
  fi
  warn "Claude Code $have is older than $CLAUDE_CODE_MIN_VERSION, which otto-workbench's AGENTS.md support assumes — it reads AGENTS.md natively from there. Run: claude update"
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
  step_claude_version

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
