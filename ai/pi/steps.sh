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

# _pi_rule_reaches_pi FILE — true when FILE is an always-on rule that is not
# scoped away from Pi.
#
# Two exclusions, for different reasons. A `paths:` rule is loaded by Claude
# Code only when a matching file is touched, and Pi has no path scoping for
# context files — carrying it would mean Ansible rules in a Go repo, in every
# session. That half is Pi's own question, which is why it lives here rather
# than in rule_harness_ok. The other half — a `harness:` list that omits pi —
# is the same opt-out claude-rules and validate-rules ask about, so it is the
# shared predicate.
_pi_rule_reaches_pi() {
  local paths
  paths="$(frontmatter_field "$1" paths)"
  # `paths: []` reads back as the literal `[]` — frontmatter_field keeps an
  # inline list's brackets on purpose, so `harness: []` stays distinguishable
  # from an absent key. An empty list scopes the rule to nothing, which is not a
  # scope, so it is normalized away here rather than read as "path-scoped".
  paths="${paths//[[:space:]]/}"
  if [[ "$paths" == "[]" ]]; then
    paths=""
  fi
  [[ -n "$paths" ]] && return 1
  rule_harness_ok "$1" pi
}

# _pi_rule_body FILE — prints FILE with its opening frontmatter block removed.
#
# Pi has no use for `paths:` or `harness:`, and a stray YAML block mid-document
# reads as content. A file with no frontmatter prints unchanged.
_pi_rule_body() {
  awk 'NR==1 && /^---$/ { in_fm=1; next }
       in_fm && /^---$/ { in_fm=0; next }
       !in_fm { print }' "$1"
}

# step_pi_guidelines — generates Pi's global context file from the rules
# directory Claude Code loads.
#
# Pi reads exactly one context file per directory, first match of
# AGENTS.override.md, AGENTS.md, AGENTS.MD, CLAUDE.md, CLAUDE.MD — so unlike
# skills, which symlink into both harnesses unchanged, this side has to be
# concatenated. The operator's escape hatch is the first of those names, which
# this step never writes and never removes.
#
# The source is $CLAUDE_RULES_DIR rather than the workbench's rule sources
# because three layers merge into what this machine actually applies — repo
# defaults, the user override layer, and machine-local *.local.md files — and
# only that directory holds all three. workbench.md there is generated by
# claude-rules and symlinked from nothing at all. Reading the sources instead
# would quietly drop exactly the rules most worth carrying.
#
# The ordering this depends on is enforced, not hoped for: ai/steps.sh's
# ai_tool_order puts claude ahead of pi for both entry points, so
# step_claude_rules has filled $CLAUDE_RULES_DIR before this runs.
#
# ceiling: reading Claude's installed rules root at all is the shortcut. Upgrade
# to a harness-neutral installed rules root once a Pi-only machine needs this,
# or once Pi grows a rules directory of its own.
step_pi_guidelines() {
  if [[ ! -d "$CLAUDE_RULES_DIR" ]]; then
    warn "$CLAUDE_RULES_DIR holds no rules — skipping Pi's context file"
    return 0
  fi

  local -a included=()
  local file synced=false
  # Sorted, not glob order: an unsorted walk rewrites the file on every sync,
  # and a context file that changes with no rule change is noise in every diff
  # an operator takes of it.
  while IFS= read -r file; do
    # A dangling symlink or an otherwise unreadable entry is skipped rather than
    # read. frontmatter_field answers empty for a path that is not there, so such
    # an entry passes _pi_rule_reaches_pi and then kills awk in _pi_rule_body,
    # taking the whole sync down with it — before sync_claude, the only thing
    # that prunes a dangling rule link, has run. Every later sync then dies in
    # the same place. `claude-rules add` and a hand-deleted rule source both
    # produce this state between syncs.
    if [[ ! -f "$file" || ! -r "$file" ]]; then
      warn "$file is not a readable rule file — leaving it out of Pi's context file"
      continue
    fi
    if ! _pi_rule_reaches_pi "$file"; then
      continue
    fi
    included+=("$file")
    # Everything claude-rules installs is either a symlink into the workbench or
    # the workbench.md it generates; a *.local.md regular file is a machine-local
    # addition made by `claude-rules add`, which does its own mkdir -p. An
    # included set that is nothing but those is a rules directory Claude has
    # never synced into, not a machine whose rules are all local.
    if [[ "$file" != *.local.md ]]; then synced=true; fi
  done < <(find "$CLAUDE_RULES_DIR" -maxdepth 1 -name '*.md' | sort)

  if [[ ${#included[@]} -eq 0 ]]; then
    warn "$CLAUDE_RULES_DIR holds no rules that reach Pi — skipping Pi's context file"
    return 0
  fi

  if [[ "$synced" != true ]]; then
    warn "$CLAUDE_RULES_DIR holds only machine-local rules — run claude-rules sync before Pi's context file can be written"
    return 0
  fi

  mkdir -p "$PI_AGENT_DIR"

  # Composed into a temp file and moved into place, so a failure leaves the
  # previous context file rather than a truncated one. rc carries the failure out
  # of the group — the tmp is removed and the step fails, rather than a partial
  # AGENTS.md.tmp outliving the run beside a missing AGENTS.md.
  local tmp="$PI_CONTEXT_FILE.tmp"
  local rc=0
  {
    printf '<!-- Generated by otto-workbench (step_pi_guidelines) from %s\n' "$CLAUDE_RULES_DIR"
    printf '     Edits here are lost on the next sync. To replace this file\n'
    printf '     entirely, write %s/AGENTS.override.md — Pi\n' "$PI_AGENT_DIR"
    printf '     reads that in preference and the workbench never touches it. -->\n\n'
    [[ -f "$PI_CONTEXT_HEAD_SRC" ]] && { cat "$PI_CONTEXT_HEAD_SRC"; echo; }
    for file in "${included[@]}"; do
      printf -- '<!-- ─── %s ─── -->\n\n' "$(basename "$file")"
      _pi_rule_body "$file" || rc=1
      echo
    done
  } > "$tmp"

  if (( rc != 0 )); then
    rm -f "$tmp"
    err "Could not read every rule — leaving $PI_CONTEXT_FILE as it was"
    return 1
  fi
  mv "$tmp" "$PI_CONTEXT_FILE"

  [[ "${WORKBENCH_SYNC:-}" != true ]] \
    && success "Pi guidelines → $PI_CONTEXT_FILE (${#included[@]} rules)" || true
  return 0
}

# step_install_pi — installs Pi via its own installer unless the managed
# launcher is already there.
#
# Pi ships as an npm package with no Homebrew formula, and its installer owns
# the managed install root and the launcher symlink that `pi update` later
# replaces — so `npm install -g` would produce a copy Pi cannot update itself.
# The installer prompts on /dev/tty for a missing Node and for a PATH edit it
# does not need here, and skips both when no terminal is attached.
#
# Gated on PI_NATIVE_BIN rather than `command -v pi`: an npm-global copy earlier
# on PATH answers the latter, and a machine holding one skipped this step
# entirely while reporting success.
step_install_pi() {
  install_via_installer pi "$PI_INSTALL_URL" "Pi" "$PI_NATIVE_BIN"
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
#
# A machine without Pi is left alone rather than installed onto: sync re-applies
# config, and the install belongs to setup, where the operator chose the tool.
# Same guard sync_claude carries.
sync_pi() {
  command -v pi >/dev/null 2>&1 || { warn "pi not found in PATH — skipping"; return; }

  sync_header "pi settings → $PI_SETTINGS_FILE"
  step_pi_settings

  sync_header "pi guidelines → $PI_CONTEXT_FILE"
  step_pi_guidelines
}

register_pi_steps() {
  register_step "Install pi"    step_install_pi
  register_step "Pi settings"   step_pi_settings
  register_step "Pi guidelines" step_pi_guidelines
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Pi sync${NC}\n"
  sync_pi
  echo
  success "Pi sync complete!"
fi
