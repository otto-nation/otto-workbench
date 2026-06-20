#!/usr/bin/env bash
# Shared registry discovery, install-check, and env/auth iteration.
#
# Used by: bin/generate-tool-context, brew/summary.sh, bin/validate-registries,
#          ai/claude/steps.sh
#
# ── Tool Entry Interface ─────────────────────────────────────────────────
#
# Always required:
#   name          string          Tool or command name
#   description   string          One-line description
#   permission    bool|str|str[]  Bash permission for Claude Code settings.json:
#                   false              → no permission (internal/indirect tools)
#                   true               → Bash(name:*)
#                   "cmd"              → Bash(cmd:*)  (CLI differs from registry name)
#                   ["Bash(cmd:*)"]    → verbatim patterns (granular subcommand control)
#   visibility    enum            AI context visibility + rendering style:
#                   full   → full entry in tools.generated.md (heading, description, when_to_use, usage)
#                   brief  → compact one-liner (name + description)
#                   hidden → omitted from AI context
#
# Required when visibility: full (forbidden otherwise):
#   when_to_use   string          When the AI should reach for this tool
#   usage         string          Example invocations
#
# Optional:
#   docs          string          URL to external documentation
#   brew_name     string          Brewfile formula name when it differs from tool name
#   commands      object[]        Subcommand definitions (otto-workbench only)
#   auth          object          Auth block with env_var, setup_url, prefix
#
# ── Functions ─────────────────────────────────────────────────────────────
#
#   is_installed NAME             — returns 0 if NAME is in PATH
#   collect_registries ARRAY_REF SCAN_DIR [BREW_DIR]
#                                 — populates array with deduplicated registry paths
#   collect_registry_permissions ARRAY_REF SCAN_DIR [BREW_DIR]
#                                 — populates array with Bash(...) permission patterns
#                                   from tools that declare an allow field
#   registry_passes_install_check FILE
#                                 — returns 0 if registry should be rendered
#   iter_registry_env FILE CALLBACK
#                                 — calls CALLBACK var comment default_val setup_url prefix
#                                   for each env[] entry in FILE
#   iter_registry_auth FILE CALLBACK
#                                 — calls CALLBACK name env_var setup_url prefix
#                                   for each tool with an auth block in FILE
#                                   (respects install_check: skips uninstalled tools)

# Known tool entry fields — used by validate-registries to reject unknown keys
# shellcheck disable=SC2034
KNOWN_TOOL_FIELDS="name description when_to_use permission visibility usage docs brew_name commands auth"

# is_installed NAME — returns 0 if NAME is found in PATH
is_installed() { command -v "$1" >/dev/null 2>&1; }

# collect_registries ARRAY_REF SCAN_DIR [BREW_DIR]
# Populates the caller's array (via nameref) with deduplicated registry paths.
# SCAN_DIR: root directory to glob for */registry.yml, /*/*/registry.yml, and *.env.yml
# BREW_DIR: directory to search for *.registry.yml stacks (defaults to SCAN_DIR/brew)
collect_registries() {
  local -n _out_arr=$1
  local scan_dir="$2"
  local brew_dir="${3:-$scan_dir/brew}"

  _out_arr=()
  local -a raw=()

  # Component registries (top-level + nested)
  for f in "$scan_dir"/*/registry.yml "$scan_dir"/*/*/registry.yml; do
    if [[ -f "$f" ]]; then
      raw+=("$f")
    fi
  done

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
    # Expand ~ to $HOME
    check_symlink="${check_symlink/#\~/$HOME}"
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
# Respects install_check: skips tools not in PATH when install_check is true.
iter_registry_auth() {
  local file="$1" cb="$2"
  [[ -f "$file" ]] || return 0
  local install_check filter=""
  install_check=$(yq '.meta.install_check // false' "$file")
  [[ "$install_check" == "true" ]] && filter="is_installed"

  local count i
  count=$(yq '.tools | length' "$file")
  for (( i=0; i<count; i++ )); do
    local env_var
    env_var=$(yq ".tools[$i].auth.env_var // \"\"" "$file")
    [[ -n "$env_var" && "$env_var" != "null" ]] || continue

    local name
    name=$(yq ".tools[$i].name" "$file")
    if [[ -n "$filter" ]] && ! "$filter" "$name"; then continue; fi

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
# patterns derived from tools' permission field. See Tool Entry Interface above.
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
