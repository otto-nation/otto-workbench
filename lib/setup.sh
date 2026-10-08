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
    success "$label already installed"
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
# own install script at URL, announcing it as LABEL and returning non-zero with
# a pointer to URL when curl is missing or the installer fails. The install is
# skipped when CMD is already in PATH.
#
# The counterpart to install_cask for a tool whose artifact is a bare
# executable: the installer's curl download carries no com.apple.quarantine
# attribute, so Gatekeeper never asks the question a cask's bare Mach-O cannot
# answer. Such installers also self-update, which a cask does not.
#
# There was once an optional MANAGED_BIN argument that replaced the `command -v`
# guard with a test on the installer's own launcher path, so a cask or an
# npm-global of the same name could not suppress the install. Both installers
# here land where the machine decides — npm's global prefix, Homebrew's on a
# machine whose Node came from there — so no such path exists to name, and a
# guard on one the installer never writes can never be satisfied: the step
# reinstalls on every run. Reintroduce it only for an installer that documents
# a fixed target, and cover it with a test at the same time.
install_via_installer() {
  local cmd="$1" url="$2" label="$3"
  if command -v "$cmd" >/dev/null 2>&1; then
    success "$label already installed"
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

# ensure_tool_path — appends the directories user-installed tools live in to
# PATH, each only if it exists and is not already there.
#
# Sync decides which zsh snippets to deploy by `command -v` on each snippet's
# requires-cmd, and removes a snippet whose command it cannot find. Its PATH is
# whatever launched it: an interactive shell has these dirs from ~/.zshrc and
# the loader, but launchd, systemd, ssh, or `sudo -i` hand over a system PATH
# without them — and a sync from there deletes mise.zsh, pi.zsh and the rest
# for tools that are installed. Appended, not prepended, so a PATH that already
# orders these dirs keeps its order and only gains what it was missing.
ensure_tool_path() {
  local dir
  for dir in "$LOCAL_BIN_DIR" "$PI_BIN_DIR" "$MISE_SHIMS_DIR" \
             "$HOMEBREW_BIN_DIR_MACOS" "$HOMEBREW_BIN_DIR_LINUX"; do
    [[ -d "$dir" ]] || continue
    [[ ":$PATH:" == *":$dir:"* ]] || PATH="$PATH:$dir"
  done
  export PATH
}

# _cmd_runnable CMD — succeeds when CMD resolves on PATH to something that can
# run. A mise shim with no active version resolves but only prints an error, so
# a shim counts only when mise can name the binary behind it.
#
# That state is ordinary: a tool one project pins in its own mise config is
# installed and shimmed, but has no global version, so the shim fails in every
# other directory.
_cmd_runnable() {
  local cmd="$1" path
  path="$(command -v "$cmd" 2>/dev/null)" || return 1
  [[ "$path" == "$MISE_SHIMS_DIR/"* ]] || return 0
  command -v mise >/dev/null 2>&1 || return 1
  mise which "$cmd" >/dev/null 2>&1
}

# _bootstrap_mise — installs mise with its own installer, for a machine that
# has neither Homebrew nor mise, and puts it on PATH for the rest of this run.
# Returns non-zero, having said why, when the install fails or leaves no mise
# to run.
#
# Core components fall back to mise for their tools, and mise is an optional
# component installed after them, so on a fresh Linux account every one of
# those installs found no installer at all. Bootstrapping here is what lets
# the fallback work on the first run instead of the second.
_bootstrap_mise() {
  install_via_installer mise "$MISE_INSTALL_URL" mise || return 1
  # Only the directory mise's installer writes to. ensure_tool_path would also
  # put an off-PATH Homebrew on PATH, switching the caller to brew halfway
  # through a decision it made because there was none.
  case ":$PATH:" in
    *":$LOCAL_BIN_DIR:"*) ;;
    *) export PATH="$LOCAL_BIN_DIR:$PATH" ;;
  esac
  if ! command -v mise >/dev/null 2>&1; then
    warn "mise's installer ran but left no mise on PATH (looked in $LOCAL_BIN_DIR) — open a new shell and re-run"
    return 1
  fi
}

# install_brew_or_mise CMD FORMULA MISE_TOOL LABEL — installs LABEL with
# `brew install FORMULA` where Homebrew is available, and otherwise with
# `mise use -g MISE_TOOL`, installing mise itself first when neither installer
# is present. Skipped when CMD is already runnable from PATH — a mise shim with
# no active version does not count. Returns non-zero
# with both install commands named when neither installer is present or the
# install fails.
#
# The mise path exists for machines without Homebrew — an unprivileged user on
# a Linux host has no writable brew prefix, but mise installs into $HOME. After
# a mise install the tool lives behind a shim that is not on PATH until the next
# login, so the shims dir is prepended for the rest of this run: the steps that
# follow look the tool up with command -v.
install_brew_or_mise() {
  local cmd="$1" formula="$2" mise_tool="$3" label="$4"
  local manual="brew install $formula, or: mise use -g $mise_tool"
  local looked="PATH" doctor
  if _cmd_runnable "$cmd"; then
    success "$label already installed"
    return
  fi
  # Unlike the _cmd_runnable check above, a bare `command -v` is what we want
  # here: a mise shim with no active version still means an installer is
  # present, so bootstrapping mise again would be pointless.
  if ! command -v brew >/dev/null 2>&1 && ! command -v mise >/dev/null 2>&1; then
    # A failed bootstrap falls through to the neither-installer warning below.
    _bootstrap_mise || true
  fi

  if command -v brew >/dev/null 2>&1; then
    info "Installing $label via Homebrew..."
    if ! brew install "$formula"; then
      warn "Homebrew could not install $label — install it manually: $manual"
      return 1
    fi
    doctor="brew doctor"
  elif command -v mise >/dev/null 2>&1; then
    info "Installing $label via mise..."
    if ! mise use -g "$mise_tool"; then
      warn "mise could not install $label — install it manually: $manual"
      return 1
    fi
    local shims="$MISE_SHIMS_DIR"
    case ":$PATH:" in
      *":$shims:"*) ;;
      *) export PATH="$shims:$PATH" ;;
    esac
    looked="$shims"
    doctor="mise doctor"
  else
    warn "Neither Homebrew nor mise found — install $label manually: $manual"
    return 1
  fi
  # Either installer can exit 0 and still leave $cmd unresolved (a keg-only
  # formula, a differently named binary, a shim that was never written).
  if ! command -v "$cmd" >/dev/null 2>&1; then
    warn "$label was installed but $cmd is not on PATH (looked in $looked) — open a new shell or check: $doctor"
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
