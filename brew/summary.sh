#!/usr/bin/env bash
# Post-install summary for the brew component.
# Sourced by install.sh after all components run — defines print_brew_summary().
# No top-level execution; safe to source without side effects.

# shellcheck source=../lib/registries.sh
. "$WORKBENCH_DIR/lib/registries.sh"

# _brew_env_entry — callback for iter_registry_env; prints ANSI-colored env var.
# Uses dynamic scoping: reads/writes `found` from the calling function.
_brew_env_entry() {
  local var="$1" _comment="$2" default_val="$3" setup_url="$4" prefix="$5"

  if [[ "$found" == false ]]; then
    echo
    echo -e "  ${CYAN}Environment setup${NC}"
    echo -e "  ${DIM}  Add these to $ENV_LOCAL_FILE:${NC}"
    found=true
  fi

  local val=""
  if [[ -n "$prefix" && "$prefix" != "null" ]]; then
    val="$prefix"
  elif [[ -n "$default_val" && "$default_val" != "null" ]]; then
    val="$default_val"
  fi
  echo -e "  ${DIM}  export ${var}=${val}${NC}"
  if [[ -n "$setup_url" && "$setup_url" != "null" ]]; then
    echo -e "  ${DIM}    → $setup_url${NC}"
  fi
}

# _brew_auth_entry — callback for iter_registry_auth; prints ANSI-colored auth var.
# Uses dynamic scoping: reads/writes `found` from the calling function.
_brew_auth_entry() {
  local _name="$1" env_var="$2" setup_url="$3" prefix="$4"

  if [[ "$found" == false ]]; then
    echo
    echo -e "  ${CYAN}Environment setup${NC}"
    echo -e "  ${DIM}  Add these to $ENV_LOCAL_FILE:${NC}"
    found=true
  fi

  local val=""
  [[ -n "$prefix" && "$prefix" != "null" ]] && val="$prefix"
  echo -e "  ${DIM}  export ${env_var}=${val}${NC}"
  if [[ -n "$setup_url" && "$setup_url" != "null" ]]; then
    echo -e "  ${DIM}    → $setup_url${NC}"
  fi
}

# _brew_env_entries — prints env setup instructions from registry env + auth blocks.
# Scans all registries and shows env vars the user may need to configure.
# Respects install_check: skips registries/tools not installed.
_brew_env_entries() {
  local found=false

  local -a registries=()
  collect_registries registries "$WORKBENCH_DIR"

  for reg in "${registries[@]}"; do
    registry_passes_install_check "$reg" || continue
    iter_registry_env "$reg" _brew_env_entry
    iter_registry_auth "$reg" _brew_auth_entry
  done
}

# print_brew_summary — prints next steps for day-to-day Homebrew usage.
print_brew_summary() {
  echo
  echo -e "  ${CYAN}Homebrew${NC}"
  echo -e "  ${DIM}  After installing new packages, sync the Brewfile:${NC}"
  echo -e "  ${DIM}  \$ task --global brew:dump${NC}"
  echo -e "  ${DIM}  Optional stacks: brew bundle --file=brew/<category>/<stack>.Brewfile${NC}"

  _brew_env_entries
}
