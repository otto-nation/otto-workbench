#!/usr/bin/env bash
# Install workflow helpers: step registration, requirement checks, cask and
# remote-installer installs.
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

# run_remote_installer URL — downloads the install script at URL and runs it,
# returning non-zero when either the download or the script fails. Prints
# nothing: the caller owns the message.
#
# The pipeline runs under pipefail because a pipeline otherwise reports only its
# last command's status. A curl that 404s prints nothing, bash reads the empty
# script and exits 0, and a download that never happened becomes
# indistinguishable from a completed install — which for a caller that removes
# the previous copy afterwards is the difference between a swap and a machine
# left with neither.
run_remote_installer() {
  ( set -o pipefail; curl -fsSL "$1" | bash )
}

# install_cask CMD CASK LABEL MANUAL_URL — installs CASK through Homebrew when
# CMD is not already in PATH, announcing it as LABEL and returning non-zero
# with a pointer to MANUAL_URL when Homebrew is missing or the install fails.
#
# For a cask whose artifact is an app bundle. brew stamps com.apple.quarantine
# on what it downloads, and a bundle carries a notarization ticket stapled to
# it, so Gatekeeper clears the first launch offline and the user sees at most
# the ordinary "downloaded from the Internet" prompt. A ticket cannot be stapled
# to a bare executable — stapling needs a bundle, a dmg, or a pkg — so a cask
# shipping one is refused outright the first time it runs, offering Move to
# Trash and nothing else. Install those with run_remote_installer or the
# vendor's own installer instead.
install_cask() {
  local cmd="$1" cask="$2" label="$3" manual_url="$4"
  if command -v "$cmd" >/dev/null 2>&1; then
    success "$cmd already installed"
    return
  fi
  require_command brew "Homebrew not found — install $label manually: $manual_url" || return
  info "Installing $label..."
  if ! brew install --cask "$cask"; then
    warn "Homebrew could not install $label — install it manually: $manual_url"
    return 1
  fi
  success "$label installed"
}

# install_via_installer CMD URL LABEL — installs LABEL by running the vendor's
# own install script at URL when CMD is not already in PATH, returning non-zero
# with a pointer to URL when curl is missing or the installer fails.
#
# The counterpart to install_cask for a tool whose artifact is a bare
# executable: the installer's curl download carries no com.apple.quarantine
# attribute, so Gatekeeper never asks the question a cask's bare Mach-O cannot
# answer. Such installers also self-update, which a cask does not.
install_via_installer() {
  local cmd="$1" url="$2" label="$3"
  if command -v "$cmd" >/dev/null 2>&1; then
    success "$cmd already installed"
    return
  fi
  require_command curl "curl not found — install $label manually: $url" || return
  info "Installing $label..."
  if ! run_remote_installer "$url"; then
    warn "$label's installer failed — install it manually: $url"
    return 1
  fi
  success "$label installed"
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
