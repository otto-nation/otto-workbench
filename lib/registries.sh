#!/usr/bin/env bash
# doc-group: registry
# Registry discovery, install-check gating, and env/auth iteration.
#
# The schema these functions read — the meta block, the tool entry fields, the
# `*.env.yml` shape, and the cross-validation modes — is documented once, in
# [Registries](registries.md#schema). `KNOWN_TOOL_FIELDS` and
# `KNOWN_COMMAND_FIELDS` below are what `validate-registries` rejects unknown
# keys against.
#
# Sourced directly by its consumers — `bin/local/generate-tool-context`,
# `bin/local/validate-registries`, `brew/summary.sh`, `summary.sh`, and
# `ai/claude/steps.sh`. Not in the `ui.sh` facade. It loads `roots.sh` itself when
# the caller has not already sourced `constants.sh`, since an
# `install_check_symlink` value may name a workbench root.

# install_check_symlink values may reference the workbench roots, so load them
# when the caller has not already sourced lib/constants.sh — tests source this
# module on its own.
if [[ -z "${WORKBENCH_STATE_DIR:-}" ]]; then
  # shellcheck source=./roots.sh
  . "$(dirname "${BASH_SOURCE[0]}")/roots.sh"
fi

# Known tool entry fields — used by validate-registries to reject unknown keys
# shellcheck disable=SC2034
KNOWN_TOOL_FIELDS="name description when_to_use permission visibility usage docs brew_name commands auth"
# Known command entry fields (within a tool's commands[] array)
# shellcheck disable=SC2034
KNOWN_COMMAND_FIELDS="name description scope when detail"

# is_installed NAME — returns 0 if NAME is found in PATH
is_installed() { command -v "$1" >/dev/null 2>&1; }

# collect_component_registries ARRAY_REF SCAN_DIR — the component `registry.yml`
# files under a root. ARRAY_REF names the caller's array, which is replaced with
# the paths found one and two directories below SCAN_DIR, in glob order.
# SCAN_DIR is the root those globs are anchored at; a root holding none of them
# leaves the array empty rather than filling it with unexpanded patterns.
#
# Split out of `collect_registries` because `bin/local/generate-public-surface`
# needs this set on its own: it filters by the package that owns each registry,
# and must not see the `*.env.yml` and brew stack files `collect_registries` adds
# on top, since brew tools are not part of the public surface. Filtering those
# back out in the caller would only point the same coupling the other way. With
# the glob written out in both places, a change to the depth a component registry
# may live at reached the tool context and not the surface snapshot, and the
# snapshot regenerated smaller with no error from either script.
collect_component_registries() {
  local -n __components_out=$1
  local scan_dir="$2"
  local f

  __components_out=()
  for f in "$scan_dir"/*/registry.yml "$scan_dir"/*/*/registry.yml; do
    if [[ -f "$f" ]]; then
      __components_out+=("$f")
    fi
  done
}

# collect_registries ARRAY_REF SCAN_DIR [BREW_DIR]
# Populates the caller's array (via nameref) with deduplicated registry paths.
#
# SCAN_DIR: root directory to glob for */registry.yml, /*/*/registry.yml, and *.env.yml
# BREW_DIR: directory to search for *.registry.yml stacks (defaults to SCAN_DIR/brew)
collect_registries() {
  local -n _out_arr=$1
  local scan_dir="$2"
  local brew_dir="${3:-$scan_dir/brew}"

  _out_arr=()

  # Component registries (top-level + nested). First, because
  # collect_component_registries assigns to the array rather than appending.
  local -a raw=()
  collect_component_registries raw "$scan_dir"

  # Consumer-owned env files (colocated with the code that reads the vars)
  while IFS= read -r -d '' f; do
    raw+=("$f")
  done < <(find "$scan_dir" -name '*.env.yml' -not -path '*/.git/*' -print0 | sort -z)

  # Brew stack registries
  if [[ -d "$brew_dir" ]]; then
    while IFS= read -r -d '' f; do
      raw+=("$f")
    done < <(find "$brew_dir" -mindepth 2 -maxdepth 2 -name '*.registry.yml' -print0 | sort -z)
  fi

  # Deduplicate by realpath
  local -A seen=()
  local f real
  for f in "${raw[@]}"; do
    real=$(realpath "$f" 2>/dev/null || echo "$f")
    [[ -n "${seen[$real]:-}" ]] && continue
    seen[$real]=1
    _out_arr+=("$f")
  done
}

# registry_passes_install_check FILE — returns 0 if the registry should be rendered.
# Checks meta.install_check and meta.install_check_command.
registry_passes_install_check() {
  local file="$1"
  local install_check
  install_check=$(yq '.meta.install_check // false' "$file")
  [[ "$install_check" == "true" ]] || return 0

  # Symlink-based check: pass if a symlink's target contains the expected string.
  # Used by registries whose relevance depends on a runtime choice (e.g. Docker runtime).
  local check_symlink check_contains
  check_symlink=$(yq '.meta.install_check_symlink // ""' "$file")
  check_contains=$(yq '.meta.install_check_symlink_contains // ""' "$file")
  if [[ -n "$check_symlink" && "$check_symlink" != "null" ]]; then
    # Expand ~ to $HOME, and the workbench roots to their resolved values.
    # Literal substitution rather than eval — the value comes from a registry
    # file, and only these three names are recognised.
    check_symlink="${check_symlink/#\~/$HOME}"
    check_symlink="${check_symlink//\$\{WORKBENCH_CONFIG_DIR\}/$WORKBENCH_CONFIG_DIR}"
    check_symlink="${check_symlink//\$\{WORKBENCH_STATE_DIR\}/$WORKBENCH_STATE_DIR}"
    check_symlink="${check_symlink//\$\{WORKBENCH_CACHE_DIR\}/$WORKBENCH_CACHE_DIR}"
    local symlink_target
    symlink_target=$(readlink "$check_symlink" 2>/dev/null || true)
    [[ "$symlink_target" == *"$check_contains"* ]] && return 0 || return 1
  fi

  # Command-based check: pass if a specific command is in PATH.
  local check_cmd
  check_cmd=$(yq '.meta.install_check_command // ""' "$file")
  if [[ -n "$check_cmd" && "$check_cmd" != "null" ]]; then
    is_installed "$check_cmd"
    return $?
  fi

  # Fallback: pass if any tool from the registry is installed
  local count i
  count=$(yq '.tools | length' "$file")
  for (( i=0; i<count; i++ )); do
    local name
    name=$(yq ".tools[$i].name" "$file")
    if is_installed "$name"; then
      return 0
    fi
  done
  return 1
}

# iter_registry_env FILE CALLBACK
# Calls CALLBACK var comment default_val setup_url prefix for each env[] entry.
iter_registry_env() {
  local file="$1" cb="$2"
  [[ -f "$file" ]] || return 0
  local has_env
  has_env=$(yq '. | has("env")' "$file")
  [[ "$has_env" == "true" ]] || return 0

  local count i
  count=$(yq '.env | length' "$file")
  for (( i=0; i<count; i++ )); do
    local var comment default_val setup_url prefix
    var=$(yq ".env[$i].var // \"\"" "$file")
    [[ -n "$var" && "$var" != "null" ]] || continue

    comment=$(yq ".env[$i].comment // \"\"" "$file")
    default_val=$(yq ".env[$i].default // \"\"" "$file")
    setup_url=$(yq ".env[$i].setup_url // \"\"" "$file")
    prefix=$(yq ".env[$i].prefix // \"\"" "$file")

    "$cb" "$var" "$comment" "$default_val" "$setup_url" "$prefix"
  done
}

# iter_registry_auth FILE CALLBACK
# Calls CALLBACK name env_var setup_url prefix for each tool with an auth block.
iter_registry_auth() {
  local file="$1" cb="$2"
  [[ -f "$file" ]] || return 0

  local count i
  count=$(yq '.tools | length' "$file")
  for (( i=0; i<count; i++ )); do
    local env_var
    env_var=$(yq ".tools[$i].auth.env_var // \"\"" "$file")
    [[ -n "$env_var" && "$env_var" != "null" ]] || continue

    local name
    name=$(yq ".tools[$i].name" "$file")

    local setup_url prefix
    setup_url=$(yq ".tools[$i].auth.setup_url // \"\"" "$file")
    prefix=$(yq ".tools[$i].auth.prefix // \"\"" "$file")

    "$cb" "$name" "$env_var" "$setup_url" "$prefix"
  done
}

# _collect_tool_permission ARRAY_REF FILE INDEX
_collect_tool_permission() {
  local -n __tool_perms=$1
  local file="$2" i="$3"
  local perm_tag perm_val name

  perm_tag=$(yq ".tools[$i].permission | tag" "$file")

  case "$perm_tag" in
    '!!null') return 0 ;;
    '!!bool')
      perm_val=$(yq ".tools[$i].permission" "$file")
      [[ "$perm_val" == "true" ]] || return 0
      name=$(yq ".tools[$i].name" "$file")
      __tool_perms+=("Bash($name:*)")
      ;;
    '!!str')
      perm_val=$(yq ".tools[$i].permission" "$file")
      [[ -n "$perm_val" ]] || return 0
      __tool_perms+=("Bash($perm_val:*)")
      ;;
    '!!seq')
      local j arr_len entry
      arr_len=$(yq ".tools[$i].permission | length" "$file")
      for (( j=0; j<arr_len; j++ )); do
        entry=$(yq ".tools[$i].permission[$j]" "$file")
        __tool_perms+=("$entry")
      done
      ;;
  esac
}

# collect_registry_permissions ARRAY_REF SCAN_DIR [BREW_DIR]
# Populates the caller's array (via nameref) with Claude Code Bash permission
# patterns derived from tools' permission field, one of the tool entry fields
# described in this module's header comment above.
collect_registry_permissions() {
  local _perms_var=$1
  local -n __perms_out=$1
  local scan_dir="$2"
  local brew_dir="${3:-$scan_dir/brew}"

  __perms_out=()
  local -a registries=()
  collect_registries registries "$scan_dir" "$brew_dir"

  local file count i
  for file in "${registries[@]}"; do
    [[ -f "$file" ]] || continue
    count=$(yq '.tools | length' "$file" 2>/dev/null) || continue
    [[ "$count" -gt 0 ]] || continue

    for (( i=0; i<count; i++ )); do
      _collect_tool_permission "$_perms_var" "$file" "$i"
    done
  done
}

# collect_claude_env_vars ARRAY_REF SCAN_DIR [BREW_DIR]
# Populates the caller's array (via nameref) with the names of the env vars
# declared by every registry whose meta block sets `claude_env: true` — the
# variables `ai/claude/steps.sh` mirrors from `~/.env.local` into the live
# `~/.claude/settings.json`. Scanning and the brew-directory default match
# `collect_registry_permissions` above.
#
# The flag is opt-in per registry rather than a sweep of every declaration
# because the two files have different audiences: `~/.env.local` holds API keys
# and is the operator's alone, while `~/.claude/settings.json` is written 0644
# and read by every Claude Code session. Only a variable a registry has
# volunteered crosses over. Install checks are deliberately not consulted — what
# reaches the settings file is decided by what `~/.env.local` actually sets, so a
# registry gated on a tool this machine lacks contributes nothing anyway.
collect_claude_env_vars() {
  local -n __env_out=$1
  local scan_dir="$2"
  local brew_dir="${3:-$scan_dir/brew}"

  __env_out=()
  local -a registries=()
  collect_registries registries "$scan_dir" "$brew_dir"

  local file flagged count i var
  for file in "${registries[@]}"; do
    [[ -f "$file" ]] || continue
    flagged=$(yq '.meta.claude_env // false' "$file" 2>/dev/null) || continue
    [[ "$flagged" == "true" ]] || continue

    count=$(yq '.env | length' "$file" 2>/dev/null) || continue
    [[ "$count" -gt 0 ]] || continue

    for (( i=0; i<count; i++ )); do
      var=$(yq ".env[$i].var // \"\"" "$file")
      [[ -n "$var" && "$var" != "null" ]] || continue
      __env_out+=("$var")
    done
  done
}
