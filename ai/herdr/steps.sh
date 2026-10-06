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
}

# step_update_herdr — moves herdr to the current release.
#
# Plain `herdr update` leaves compatible running servers alive; --handoff is
# experimental and is never passed. Non-fatal: an offline machine still gets
# the rest of its config, as with step_update_pi.
step_update_herdr() {
  command -v herdr > /dev/null 2>&1 || { warn "herdr not found in PATH — skipping"; return 0; }
  if herdr update > /dev/null 2>&1; then
    [[ "${WORKBENCH_SYNC:-}" != true ]] && success "herdr is current" || true
  else
    warn "Could not update herdr — run: herdr update"
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
}

register_herdr_steps() {
  register_step "Install herdr"      step_install_herdr
  register_step "Update herdr"       step_update_herdr
  register_step "Herdr integrations" step_herdr_integrations
}

# ─── Standalone execution ─────────────────────────────────────────────────────

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  echo -e "${BOLD}${BLUE}Herdr sync${NC}\n"
  sync_herdr
  echo
  success "Herdr sync complete!"
fi
