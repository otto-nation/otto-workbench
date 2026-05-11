#!/usr/bin/env bash
# Post-install summary for the ai component.
# Sourced by install.sh after all components run — defines print_ai_summary().
# No top-level execution; safe to source without side effects.

# print_ai_summary — prints AI-specific summary info.
# AI_COMMAND and GH_TOKEN status are now shown by the central summary in lib/summary.sh.
print_ai_summary() {
  echo
  echo -e "  ${CYAN}AI Tasks${NC}"

  if command -v claude >/dev/null 2>&1; then
    summary_ok "Claude CLI installed"
  else
    summary_warn "Claude CLI not found — install: ${DIM}brew install claude${NC}"
  fi

  summary_info "Available commands: ${DIM}task commit, task pr:create, task review${NC}"
}
