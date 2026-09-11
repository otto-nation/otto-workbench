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

# _pi_package_repo SOURCE — prints the owner/repo a GitHub package source names.
# Prints nothing for a source that names no GitHub repo — an npm spec, a local
# path, or a repo on another host — which the gate then leaves alone.
#
# A pinned `@ref` and a trailing `.git` are both stripped: the probe addresses a
# repo, and neither is part of its name.
_pi_package_repo() {
  local source="$1"
  if [[ ! "$source" =~ github\.com[:/]([^/]+)/([^/@#]+) ]]; then
    return 0
  fi
  printf '%s/%s' "${BASH_REMATCH[1]}" "${BASH_REMATCH[2]%.git}"
}

# _pi_repo_reachable REPO — prints reachable, unreachable, or unknown for an
# owner/repo.
#
# The question the gate needs answered is whether the clone will succeed, which
# is not the same as whether this machine belongs to the owning org. A public
# user-owned repo has no org to belong to, and a deleted or team-restricted repo
# inside an org this machine does belong to still cannot be cloned.
#
# Unknown is every answer that is not a verdict: no gh, no auth, no network, or
# a scope the token lacks. It is distinct from unreachable because the two lead
# to opposite actions — a sync run offline must not withdraw a package that
# already works, and must not add one it could not verify.
_pi_repo_reachable() {
  local repo="$1" out status
  if ! command -v gh > /dev/null 2>&1; then
    printf 'unknown'
    return 0
  fi

  # No --jq: exit 0 is itself the allow signal, and repo JSON carries no field
  # worth reading here. The body is captured only so a failure's error text can
  # be matched, and is discarded on success.
  out=$(gh api "repos/$repo" 2>&1) && status=0 || status=$?

  if [[ $status -eq 0 ]]; then
    printf 'reachable'
    return 0
  fi
  # GitHub answers 404 for a private repo this token cannot see exactly as it
  # does for one that does not exist. Both mean the clone fails, so the gate
  # does not need to tell them apart.
  if [[ "$out" == *"HTTP 404"* || "$out" == *"Not Found"* ]]; then
    printf 'unreachable'
    return 0
  fi
  printf 'unknown'
}

# _pi_partition_packages ALLOWED_VAR BLOCKED_VAR — splits the template's packages
# into JSON arrays of what this machine was confirmed to reach and confirmed not to.
#
# An entry this machine cannot clone leaves Pi retrying the clone on every
# startup. Packages whose repo could not be probed land in neither array and are
# left however the live file has them.
#
# Every local here carries the __pi_ prefix for the same reason the namerefs carry
# __: a local sharing a name with the variable the caller named would shadow the
# nameref's target, and the result would be assigned to this scope rather than to
# the caller's — silently, since a nameref reports no error for it.
_pi_partition_packages() {
  local -n __allowed=$1
  local -n __blocked=$2
  local -a __pi_ok=() __pi_no=()
  local __pi_entry __pi_source __pi_repo __pi_verdict

  while IFS= read -r __pi_entry; do
    [[ -z "$__pi_entry" ]] && continue
    __pi_source=$(jq -r 'if type == "object" then .source else . end' <<< "$__pi_entry")
    __pi_repo=$(_pi_package_repo "$__pi_source")
    __pi_verdict=reachable
    [[ -n "$__pi_repo" ]] && __pi_verdict=$(_pi_repo_reachable "$__pi_repo")
    case "$__pi_verdict" in
      reachable)   __pi_ok+=("$__pi_entry") ;;
      unreachable) __pi_no+=("$__pi_entry"); skip "Pi package $__pi_source — cannot reach $__pi_repo" ;;
      *)           skip "Pi package $__pi_source — could not verify $__pi_repo is reachable" ;;
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
# is the same opt-out workbench-rules and validate-rules ask about, so it is the
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

# step_pi_guidelines — generates Pi's global context file from the merged rule
# set every harness shares.
#
# Pi reads exactly one context file per directory, first match of
# AGENTS.override.md, AGENTS.md, AGENTS.MD, CLAUDE.md, CLAUDE.MD — so unlike
# skills, which symlink into both harnesses unchanged, this side has to be
# concatenated. The operator's escape hatch is the first of those names, which
# this step never writes and never removes.
#
# resolve_rules rather than a harness's installed directory: all three layers
# this machine applies — repo defaults, the operator's overrides, and the
# generated workbench.md — resolve without any harness having been installed,
# so a machine running Pi alone gets the same context file as one running both.
step_pi_guidelines() {
  local -A layers
  resolve_rules layers

  local -a included=()
  local name file
  # Sorted, not associative-array order, which bash gives no guarantees about:
  # a context file that changes with no rule change is noise in every diff an
  # operator takes of it.
  while IFS= read -r name; do
    [[ -z "$name" ]] && continue
    file="${layers[$name]}"
    # An unreadable entry is skipped rather than read. frontmatter_field answers
    # empty for a path that is not there, so such an entry passes
    # _pi_rule_reaches_pi and then kills awk in _pi_rule_body, taking the whole
    # sync down with it. A rule source deleted by hand between syncs produces
    # this state.
    if [[ ! -f "$file" || ! -r "$file" ]]; then
      warn "$file is not a readable rule file — leaving it out of Pi's context file"
      continue
    fi
    if ! _pi_rule_reaches_pi "$file"; then
      continue
    fi
    included+=("$file")
  done < <(printf '%s\n' "${!layers[@]}" | sort)

  if [[ ${#included[@]} -eq 0 ]]; then
    warn "no rule reaches Pi — skipping Pi's context file"
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
    printf '<!-- Generated by otto-workbench (step_pi_guidelines) from the rule\n'
    printf '     layers this machine resolves:\n'
    rules_layer_roots | sed 's/^/       /'
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

# step_install_pi — installs Pi via its own installer unless pi is already on
# PATH.
#
# Pi ships as an npm package with no Homebrew formula. The installer is used
# rather than a bare `npm install -g` because it pins through the published
# npm-shrinkwrap and applies the registry min-age flags; the copy it leaves
# behind is an ordinary npm global either way, which `pi update` maintains. The
# installer prompts on /dev/tty for a missing Node and for a PATH edit it does
# not need here, and skips both when no terminal is attached.
#
# Gated on `command -v pi`, not a path: the installer writes to npm's global
# prefix, which varies by machine, so there is no fixed launcher to test by name.
step_install_pi() {
  install_via_installer pi "$PI_INSTALL_URL" "Pi"
}

# _clear_pi_extension_entry PATH — empties an extension-root slot the workbench
# owns, with the same tri-state contract as _clear_skill_entry.
#
# Returns 0 once the slot is empty — either it already was, or its content was
# just removed. Returns 1 when the slot holds something the operator wrote and
# is left untouched: a refusal, reported with warn(), not a failure. Returns 2
# when the slot was the workbench's own but the removal itself failed.
#
# Only a symlink is ever the workbench's here, because this step only ever
# writes symlinks — unlike skills, no content is generated on the fly, so there
# is no marker file and nothing to distinguish a real directory from a
# hand-written extension. ~/.pi/agent/extensions is exactly where Pi's own docs
# tell an operator to put one, so a real directory is always theirs.
_clear_pi_extension_entry() {
  local path="$1"
  if [[ -L "$path" ]]; then
    if ! workbench_symlink_owned "$path" extensions; then
      warn "$path was not installed by the workbench — leaving it in place"
      return 1
    fi
    rm -f "$path" || { err "Could not remove symlink $path"; return 2; }
    return 0
  fi
  [[ -e "$path" ]] || return 0
  warn "$path was not installed by the workbench — leaving it in place"
  return 1
}

# _prune_pi_extensions TARGET_DIR LAYERS_VAR — removes workbench-installed
# extensions the layers no longer name.
#
# Globs "$target"/* rather than "$target"/*/, because the trailing-slash form
# only matches entries that *resolve* as directories — and a retired extension
# leaves a dangling symlink, which is precisely what has to be visited.
#
# The nameref's __ prefix is mandatory: a local of the same name in this
# function would shadow the caller's array rather than reference it.
_prune_pi_extensions() {
  local target="$1"
  local -n __extension_layers=$2
  local item entry name
  for item in "$target"/*; do
    entry="${item%/}"
    [[ -L "$entry" || -d "$item" ]] || continue
    name=$(basename "$item")
    [[ -z "${__extension_layers[$name]+set}" ]] || continue

    _clear_pi_extension_entry "$entry" || continue
    [[ "${WORKBENCH_SYNC:-}" != true ]] && echo -e "  ${DIM}⊘ pruned $name${NC}" || true
  done
  return 0
}

# step_pi_extensions — installs every workbench Pi extension into Pi's global
# extension root.
#
# Pi auto-discovers ~/.pi/agent/extensions/<name>/index.ts and follows symlinks
# when it does, so one source tree reaches Pi with no copy and no compile — it
# loads the TypeScript through jiti. The same arrangement step_skills uses for
# ~/.agents/skills.
#
# Directory-form rather than a flat <name>.ts for two mechanical reasons.
# resolve_layers keys on the full basename, so a flat layout would spell the
# override sentinel <name>.ts.disabled while every other override in the
# workbench is <name>.disabled. And Pi reads a package.json inside the
# directory, so an extension that later needs npm dependencies needs no move.
#
# Supports user overrides: overrides/ai/pi/extensions/<name>/ replaces the
# default, overrides/ai/pi/extensions/<name>.disabled suppresses it.
#
# Only what the workbench installed is ever removed — see
# _clear_pi_extension_entry. An extension an operator disabled through `pi
# config` stays disabled across syncs: that writes an enablement flag into
# settings, which nothing here touches, and operator intent should win.
#
# ai/pi/extensions-cli/ is deliberately not installed. What lives there is
# passed with --extension by one pipeline and must not load in every session.
step_pi_extensions() {
  [[ -d "$PI_EXTENSIONS_SRC_DIR" ]] \
    || { warn "No Pi extensions in $PI_EXTENSIONS_SRC_DIR — skipping"; return; }
  mkdir -p "$PI_EXTENSIONS_DIR"
  [[ "${WORKBENCH_SYNC:-}" != true ]] \
    && info "Installing Pi extensions to $PI_EXTENSIONS_DIR/" || true

  local -A layers
  resolve_layers "$PI_EXTENSIONS_SRC_DIR" "$USER_PI_EXTENSIONS_DIR" "*/" layers

  _prune_pi_extensions "$PI_EXTENSIONS_DIR" layers

  local name source
  for name in "${!layers[@]}"; do
    source="${layers[$name]}"

    # Pi's own entry-point rule, checked here so a malformed override is named
    # rather than silently discovered as nothing. Pi reads package.json first,
    # then index.ts, then index.js.
    if [[ ! -f "$source/index.ts" && ! -f "$source/index.js" \
       && ! -f "$source/package.json" ]]; then
      warn "No index.ts, index.js or package.json in $source — skipping $name"
      continue
    fi

    install_symlink "$source" "$PI_EXTENSIONS_DIR/$name" "$name → pi"
  done
  return 0
}

# _pi_build_models — reads the model env vars the registries declare out of
# ~/.env.local and prints a JSON object with defaultModel + enabledModels, or {}
# when there is no default to build around. The provider prefix comes from the
# template.
#
# Which variables carry models is not written here: ai/models.env.yml declares
# them with a `role`, and collect_model_env_vars reads it, so a tier added there
# reaches Pi without this function changing. Claude Code's sync reads the same
# file through collect_claude_env_vars.
_pi_build_models() {
  # Before the registry read, so a machine with no ~/.env.local answers without
  # yq — there are no model values to be had either way, and this is the one
  # path through the function that needs nothing beyond jq.
  if [[ ! -f "$ENV_LOCAL_FILE" ]]; then
    printf '{}'
    return 0
  fi

  # Lazily, because ai/steps.sh sources this file on every CLI invocation and
  # registries.sh is not in the ui.sh facade. WORKBENCH_STABLE_DIR rather than
  # WORKBENCH_DIR so a sync run from a feature worktree reads the same
  # registries Claude Code's step does — divergence between the two is what
  # this indirection exists to remove.
  if ! declare -F collect_model_env_vars > /dev/null 2>&1; then
    # shellcheck source=../../lib/registries.sh
    . "$LIB_SRC_DIR/registries.sh"
  fi

  local -a model_vars=() model_roles=()
  collect_model_env_vars model_vars model_roles "$WORKBENCH_STABLE_DIR"

  local default_model="" i value
  local -a values=()
  for (( i=0; i<${#model_vars[@]}; i++ )); do
    value=$(read_env_local_var "${model_vars[i]}")
    values+=("$value")
    if [[ "${model_roles[i]}" == model-default ]]; then
      default_model="$value"
    fi
  done

  if [[ -z "$default_model" ]]; then
    printf '{}'
    return 0
  fi

  local provider values_json='[]'
  provider=$(jq -r '.defaultProvider // "google-vertex-claude"' "$PI_SETTINGS_SRC")
  # Guarded because bash 4.3 treats "${arr[@]}" on an empty array as unbound.
  if (( ${#values[@]} > 0 )); then
    values_json=$(printf '%s\n' "${values[@]}" | jq -Rn '[inputs]')
  fi

  # Every set model is enabled, and the default leads whatever order the
  # registry declared. An unset var is dropped rather than emitting an empty
  # entry, so a partially-configured ~/.env.local produces a shorter list
  # instead of clobbering working entries.
  #
  # Deduplicated on the way out, because two tiers may name one model — opus
  # and sonnet both pointed at the same id is a normal way to pin a machine to
  # one model. `unique` would sort, and the list reads defaultModel-first, so
  # the fold below keeps first-seen order instead.
  jq -n \
    --arg default "$default_model" \
    --arg provider "$provider" \
    --argjson values "$values_json" \
    '{ defaultModel: $default,
       enabledModels: ([$default] + $values
         | map(select(. != ""))
         | map("\($provider)/\(.)")
         | reduce .[] as $m ([]; if index($m) then . else . + [$m] end)) }'
}

# step_pi_settings — merges the workbench's managed keys into Pi's global settings.
#
# Merged rather than copied because Pi writes to the same file: `pi install`,
# `pi config` and Ctrl+S in /model all land in ~/.pi/agent/settings.json.
# Template scalars override the live file on every sync so the workbench stays
# authoritative. Model config is derived from the vars the registries declare
# with a model role, read out of ~/.env.local — the same SSOT Claude Code reads
# — and applied after template scalars so it always wins.
step_pi_settings() {
  mkdir -p "$PI_AGENT_DIR"

  local existing="{}" content
  content=$(cat "$PI_SETTINGS_FILE" 2> /dev/null) || true
  [[ -n "$content" ]] && existing="$content"

  local allowed blocked
  _pi_partition_packages allowed blocked

  local models
  models=$(_pi_build_models)

  local result
  result=$(jq -n \
    --argjson t "$(cat "$PI_SETTINGS_SRC")" \
    --argjson e "$existing" \
    --argjson allowed "$allowed" \
    --argjson blocked "$blocked" \
    --argjson models "$models" \
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

  sync_header "pi extensions → $PI_EXTENSIONS_DIR/"
  step_pi_extensions

  sync_header "pi guidelines → $PI_CONTEXT_FILE"
  step_pi_guidelines
}

register_pi_steps() {
  register_step "Install pi"     step_install_pi
  register_step "Pi settings"    step_pi_settings
  register_step "Pi extensions"  step_pi_extensions
  register_step "Pi guidelines"  step_pi_guidelines
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Pi sync${NC}\n"
  sync_pi
  echo
  success "Pi sync complete!"
fi
