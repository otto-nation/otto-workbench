#!/usr/bin/env bash
# description: Pi coding agent config
# Pi setup steps — sourced by ai/setup.sh.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

# _pi_package_org SOURCE — prints the GitHub org owning a package source.
# Prints nothing for a source with no GitHub org — an npm spec, a local path, or
# a repo on another host — which the membership gate then leaves alone.
_pi_package_org() {
  local source="$1"
  if [[ ! "$source" =~ github\.com[:/]([^/]+)/ ]]; then
    return 0
  fi
  printf '%s' "${BASH_REMATCH[1]}"
}

# _pi_org_membership ORG — prints member, nonmember, or unknown.
#
# Unknown is every answer that is not a verdict: no gh, no auth, no network, or
# a scope the token lacks. It is distinct from nonmember because the two lead to
# opposite actions — a sync run offline must not withdraw a package that already
# works, and must not add one it could not verify.
_pi_org_membership() {
  local org="$1" out status
  if ! command -v gh > /dev/null 2>&1; then
    printf 'unknown'
    return 0
  fi

  out=$(gh api "user/memberships/orgs/$org" --jq '.state' 2>&1) && status=0 || status=$?

  # A pending invitation is not membership — the clone would still be refused.
  if [[ $status -eq 0 ]]; then
    [[ "$out" == "active" ]] && printf 'member' || printf 'nonmember'
    return 0
  fi
  if [[ "$out" == *"HTTP 404"* || "$out" == *"Not Found"* ]]; then
    printf 'nonmember'
    return 0
  fi
  printf 'unknown'
}

# _pi_partition_packages ALLOWED_VAR BLOCKED_VAR — splits the template's packages
# into JSON arrays of what this machine was confirmed to reach and confirmed not to.
#
# The shared packages are private to their org, so an entry a non-member cannot
# clone leaves Pi retrying the clone on every startup. Packages whose org could
# not be checked land in neither array and are left however the live file has them.
#
# Every local here carries the __pi_ prefix for the same reason the namerefs carry
# __: a local sharing a name with the variable the caller named would shadow the
# nameref's target, and the result would be assigned to this scope rather than to
# the caller's — silently, since a nameref reports no error for it.
_pi_partition_packages() {
  local -n __allowed=$1
  local -n __blocked=$2
  local -a __pi_ok=() __pi_no=()
  local __pi_entry __pi_source __pi_org __pi_verdict

  while IFS= read -r __pi_entry; do
    [[ -z "$__pi_entry" ]] && continue
    __pi_source=$(jq -r 'if type == "object" then .source else . end' <<< "$__pi_entry")
    __pi_org=$(_pi_package_org "$__pi_source")
    __pi_verdict=member
    [[ -n "$__pi_org" ]] && __pi_verdict=$(_pi_org_membership "$__pi_org")
    case "$__pi_verdict" in
      member)    __pi_ok+=("$__pi_entry") ;;
      nonmember) __pi_no+=("$__pi_entry"); skip "Pi package $__pi_source — no active $__pi_org membership" ;;
      *)         skip "Pi package $__pi_source — could not verify $__pi_org membership" ;;
    esac
  done < <(jq -c '(.packages // [])[]' "$PI_SETTINGS_SRC")

  __allowed='[]'
  __blocked='[]'
  [[ ${#__pi_ok[@]} -gt 0 ]] && __allowed=$(printf '%s\n' "${__pi_ok[@]}" | jq -sc '.')
  [[ ${#__pi_no[@]} -gt 0 ]] && __blocked=$(printf '%s\n' "${__pi_no[@]}" | jq -sc '.')
  return 0
}

# step_pi_settings — merges the workbench's managed keys into Pi's global settings.
#
# Merged rather than copied because Pi writes to the same file: `pi install`,
# `pi config` and Ctrl+S in /model all land in ~/.pi/agent/settings.json. Scalar
# keys are seeded only when absent, so a value an extension or the operator chose
# is never overridden — which also means a changed template default never reaches
# a machine that already has the key. Delete the key there to be re-seeded.
step_pi_settings() {
  mkdir -p "$PI_AGENT_DIR"

  local existing="{}" content
  content=$(cat "$PI_SETTINGS_FILE" 2> /dev/null) || true
  [[ -n "$content" ]] && existing="$content"

  local allowed blocked
  _pi_partition_packages allowed blocked

  local result
  result=$(jq -n \
    --argjson t "$(cat "$PI_SETTINGS_SRC")" \
    --argjson e "$existing" \
    --argjson allowed "$allowed" \
    --argjson blocked "$blocked" \
    -f "$PI_SYNC_SETTINGS_JQ") \
    || { err "Failed to sync Pi settings"; return 1; }

  printf '%s\n' "$result" > "$PI_SETTINGS_FILE"
  local label="Pi settings synced"
  [[ "$existing" == "{}" ]] && label="Pi settings written"
  [[ "${WORKBENCH_SYNC:-}" != true ]] && success "$label" || true
  return 0
}

# step_pi_skills — deploys Pi agent skills from ai/claude/pi/skills/ to ~/.pi/agent/skills/.
# Replaces the AGENT_PROTOCOL_PLACEHOLDER comment with the agent protocol body from
# the corresponding agent file in ~/.claude/agents/<name>.md (installed by step_claude_agents).
step_pi_skills() {
  [[ -d "$PI_SKILLS_SRC_DIR" ]] || { skip "No Pi skills in $PI_SKILLS_SRC_DIR — skipping"; return; }
  mkdir -p "$PI_SKILLS_DIR"

  local skill_dir name agent_file dest_dir
  for skill_dir in "$PI_SKILLS_SRC_DIR"/*/; do
    [[ -d "$skill_dir" ]] || continue
    name=$(basename "$skill_dir")
    agent_file="$CLAUDE_AGENTS_DIR/${name}.md"
    dest_dir="$PI_SKILLS_DIR/$name"

    if [[ ! -f "$agent_file" ]]; then
      warn "Agent file missing for Pi skill $name: $agent_file — skipping"
      continue
    fi

    mkdir -p "$dest_dir"
    local src="$skill_dir/SKILL.md"
    [[ -f "$src" ]] || continue

    # Extract agent body (skip YAML frontmatter: everything after second ---)
    local agent_body
    agent_body=$(awk 'BEGIN{n=0} /^---$/{n++; if(n==2){found=1; next}} found{print}' "$agent_file")

    # Build output: lines before placeholder, agent body, lines after placeholder
    awk '/<!-- AGENT_PROTOCOL_PLACEHOLDER:/{exit} {print}' "$src" > "$dest_dir/SKILL.md"
    printf '%s\n' "$agent_body" >> "$dest_dir/SKILL.md"
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "Pi skill: $name" || true
  done
}

# _export_pi_config DIR — copies Pi config into DIR for tarball export.
_export_pi_config() {
  local dest="$1"
  mkdir -p "$dest"
  if [[ -f "$PI_SETTINGS_SRC" ]]; then
    cp "$PI_SETTINGS_SRC" "$dest/settings.json"
  fi
}

# sync_pi — runs all Pi sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
sync_pi() {
  sync_header "pi settings → $PI_SETTINGS_FILE"
  step_pi_settings

  sync_header "pi skills → $PI_SKILLS_DIR/"
  step_pi_skills
}

register_pi_steps() {
  register_step "Pi settings" step_pi_settings
  register_step "Pi skills"   step_pi_skills
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Pi sync${NC}\n"
  sync_pi
  echo
  success "Pi sync complete!"
fi
