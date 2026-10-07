#!/usr/bin/env bash
# description: Herdr agent multiplexer — persistent panes and agent state for Pi and Claude Code
# Herdr setup steps — sourced by ai/setup.sh and ai/steps.sh.
# All paths come from lib/constants.sh (loaded via lib/ui.sh before this file is sourced).
#
# Herdr is an operator tool: it owns agent terminals and shows which agent is
# waiting on you. Agents are deliberately given no permission to drive it, so
# it never becomes a second job facility beside job_start/run_in_background.

# Bootstrap when run standalone; when sourced, the caller has already set up the environment.
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -e
  WORKBENCH_DIR="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
  . "$WORKBENCH_DIR/lib/ui.sh"
fi

# step_install_herdr — installs herdr via its vendor installer when not on PATH.
# Keeping an installed herdr current is step_update_herdr's job.
step_install_herdr() {
  install_via_installer herdr "$HERDR_INSTALL_URL" "Herdr"
  # The installer writes LOCAL_BIN_DIR/herdr. That directory is often missing
  # from PATH until the next login, so later steps in this same run would skip.
  if ! command -v herdr > /dev/null 2>&1; then
    export PATH="$LOCAL_BIN_DIR:$PATH"
  fi
}

# step_update_herdr — moves herdr to the current release.
#
# Plain `herdr update` leaves compatible running servers alive; --handoff is
# experimental and is never passed. Stdin is closed so a restart prompt cannot
# block an unattended sync; the call's runtime is not bounded. Failure (and a
# zero-exit "was not updated") surfaces the last non-empty line of herdr's own
# output — the reason comes after any banner or progress lines, and a Homebrew
# install prints the brew upgrade command there — rather than a hardcoded
# `herdr update`. Non-fatal: an offline machine still gets the rest of its
# config, as with step_update_pi.
#
# Inside a herdr pane the update is not attempted at all: herdr refuses to
# update from within its own session ("run `herdr update` outside herdr after
# detaching"), so the attempt can only fail, and every interactive sync from a
# herdr pane reported it as a warning the operator could not act on there.
# The unattended maintenance sync runs outside any session and performs the
# update, so this is a skip, not a failure. Detected the way herdr's own
# integrations detect a pane: HERDR_ENV=1 with a pane id.
step_update_herdr() {
  command -v herdr > /dev/null 2>&1 || { warn "herdr not found in PATH — skipping"; return 0; }
  if [[ "${HERDR_ENV:-}" == 1 && -n "${HERDR_PANE_ID:-}" ]]; then
    skip "herdr update — inside a herdr session; the next sync outside one (maintenance, or after detaching) updates it"
    return 0
  fi
  local output status=0 last_line
  output=$(herdr update < /dev/null 2>&1) || status=$?
  last_line=$(printf '%s\n' "$output" | awk 'NF { line = $0 } END { print line }')
  if [[ "$status" -ne 0 || "$output" == *"was not updated"* ]]; then
    warn "herdr was not updated${last_line:+: $last_line}"
  else
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "herdr is current" || true
  fi
  return 0
}

# step_herdr_integrations — installs herdr's Pi and Claude Code integrations
# for whichever harness is configured on this machine.
#
# Herdr owns the integration content and versions it with the binary, so this
# runs after step_update_herdr and re-runs every sync — herdr asks for a
# reinstall after upgrades. The Pi file is a flat .ts the Pi extension prune
# never touches; the Claude hook entry is not in the workbench settings
# manifest, so sync-settings.jq preserves it.
step_herdr_integrations() {
  command -v herdr > /dev/null 2>&1 || { warn "herdr not found in PATH — skipping"; return 0; }
  local harness dir
  for harness in pi claude; do
    case "$harness" in
      pi)     dir="$PI_AGENT_DIR" ;;
      claude) dir="$CLAUDE_DIR" ;;
    esac
    [[ -d "$dir" ]] || continue
    if herdr integration install "$harness" > /dev/null 2>&1; then
      [[ "${WORKBENCH_SYNC:-}" != true ]] && success "herdr $harness integration installed" || true
    else
      warn "Could not install herdr's $harness integration — run: herdr integration install $harness"
    fi
  done
  return 0
}

# step_herdr_machine_note — tells the operator how to register a remote
# machine when herdr's catalog has none.
#
# Informational only: registering is left to the operator because an
# interactive `herdr machine add` may install herdr on the remote, and the
# workbench never learns a host name. Silent inside a regular sync (it would
# repeat for anyone using herdr locally on purpose) and on any error.
step_herdr_machine_note() {
  [[ "${WORKBENCH_SYNC:-}" == true ]] && return 0
  command -v herdr > /dev/null 2>&1 || return 0
  local listing count
  listing=$(herdr machine list --json 2> /dev/null) || return 0
  count=$(jq 'if type == "array" then length else -1 end' <<< "$listing" 2> /dev/null) || return 0
  if [[ "$count" == "0" ]]; then
    info "herdr: no remote machines registered — add one with: herdr machine add <ssh-host> --label <name>"
  fi
  return 0
}

# sync_herdr — runs the herdr sync steps non-interactively.
# Called automatically by otto-workbench sync via the sync_<tool> convention.
#
# A machine without herdr is left alone rather than installed onto: the
# install belongs to setup, where the operator chose the tool.
sync_herdr() {
  command -v herdr > /dev/null 2>&1 || return 0

  sync_header "herdr host"
  step_update_herdr

  sync_header "herdr integrations"
  step_herdr_integrations

  step_herdr_machine_note
}

register_herdr_steps() {
  register_step "Install herdr"      step_install_herdr
  register_step "Update herdr"       step_update_herdr
  register_step "Herdr integrations" step_herdr_integrations
  register_step "Herdr machines"     step_herdr_machine_note
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Herdr sync${NC}\n"
  sync_herdr
  echo
  success "Herdr sync complete!"
fi
