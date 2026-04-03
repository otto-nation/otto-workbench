#!/usr/bin/env bash
# Shared registry discovery, install-check, and env/auth iteration.
#
# Used by: bin/generate-tool-context, brew/summary.sh, bin/validate-registries
#
# Functions:
#   is_installed NAME             — returns 0 if NAME is in PATH
#   collect_registries ARRAY_REF SCAN_DIR [BREW_DIR]
#                                 — populates array with deduplicated registry paths
#   registry_passes_install_check FILE
#                                 — returns 0 if registry should be rendered
#   iter_registry_env FILE CALLBACK
#                                 — calls CALLBACK var comment default_val setup_url prefix
#                                   for each env[] entry in FILE
#   iter_registry_auth FILE CALLBACK
#                                 — calls CALLBACK name env_var setup_url prefix
#                                   for each tool with an auth block in FILE
#                                   (respects install_check: skips uninstalled tools)

# is_installed NAME — returns 0 if NAME is found in PATH
is_installed() { command -v "$1" >/dev/null 2>&1; }

# collect_registries ARRAY_REF SCAN_DIR [BREW_DIR]
# Populates the caller's array (via nameref) with deduplicated registry paths.
# SCAN_DIR: root directory to glob for */registry.yml and /*/*/registry.yml
# BREW_DIR: directory to search for *.registry.yml stacks (defaults to SCAN_DIR/brew)
collect_registries() {
  local -n _out_arr=$1
  local scan_dir="$2"
  local brew_dir="${3:-$scan_dir/brew}"

  _out_arr=()
  local -a raw=()

  # Component registries (top-level + nested)
  for f in "$scan_dir"/*/registry.yml "$scan_dir"/*/*/registry.yml; do
    [[ -f "$f" ]] && raw+=("$f")
  done

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
    is_installed "$name" && return 0
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
