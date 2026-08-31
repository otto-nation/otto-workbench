#!/usr/bin/env bash
# Install workflow helpers: step registration and requirement checks.
#
# Bash-only. Used primarily by `install.sh` and component setup scripts.

[[ -n "${_LIB_SETUP_SH:-}" ]] && return
_LIB_SETUP_SH=1

# Ensure dependencies are available
_setup_lib_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=output.sh
. "$_setup_lib_dir/output.sh"
# shellcheck source=prompts.sh
. "$_setup_lib_dir/prompts.sh"
unset _setup_lib_dir

# register_step NAME FN — appends a step to the STEPS array.
# STEPS must be declared as an array in the calling script before register_step is used.
register_step() { STEPS+=("${1}|${2}"); }

# run_steps — prints all registered steps upfront, then runs each with [Y/n/a] confirmation.
# Steps are read from the global STEPS array (populated via register_step).
# Prints a summary of ran/skipped counts when complete.
run_steps() {
  local total=${#STEPS[@]} index=1 ran=0 skipped=0
  local step name fn _accept_all=false _decision

  echo -e "  ${DIM}Steps:${NC}"
  local _i=1
  for step in "${STEPS[@]}"; do
    name="${step%%|*}"
    echo -e "  ${DIM}[$_i/$total] $name${NC}"
    _i=$(( _i + 1 ))
  done
  echo -e "  ${DIM}Y = run · N = skip · A = accept all remaining${NC}"

  for step in "${STEPS[@]}"; do
    name="${step%%|*}"
    fn="${step##*|}"
    echo -e "\n${DIM}[$index/$total]${NC} ${BOLD}$name${NC}"

    if [[ "$_accept_all" != true ]]; then
      confirm_step _decision "  Run this step?"
      if [[ "$_decision" == "all" ]]; then _accept_all=true; fi
    fi

    if [[ "$_accept_all" == true || "$_decision" == "yes" || "$_decision" == "all" ]]; then
      $fn
      ran=$(( ran + 1 ))
    else
      echo -e "  ${DIM}⊘ Skipped${NC}"
      skipped=$(( skipped + 1 ))
    fi

    index=$(( index + 1 ))
  done

  echo
  echo -e "${DIM}$ran run · $skipped skipped${NC}"
}

# require_command NAME [MESSAGE] — returns 1 with a warning if NAME is not in PATH.
# Caller decides whether to exit or return: require_command foo "msg" || exit 0
require_command() {
  local name=$1 msg="${2:-$1 not found in PATH — skipping}"
  command -v "$name" >/dev/null 2>&1 && return 0
  warn "$msg"
  return 1
}

# run_migrations DIR
# DEPRECATED: Use run_component_migrations from lib/migrations.sh instead.
# This function sources a single migrations.sh file with no state tracking.
# Kept for backward compatibility until all callers are migrated.
run_migrations() {
  local file="$1/migrations.sh"
  # shellcheck source=/dev/null
  [[ -f "$file" ]] && . "$file"
  return 0
}
