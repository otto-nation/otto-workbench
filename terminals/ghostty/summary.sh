#!/usr/bin/env bash
# Post-install summary for the ghostty sub-component.
# Sourced by ghostty/setup.sh — defines print_ghostty_summary().
# No top-level execution; safe to source without side effects.

# print_ghostty_summary — prints config location and useful next steps.
print_ghostty_summary() {
  summary_section "Ghostty"
  summary_info "Config: ${DIM}$GHOSTTY_CONFIG_FILE${NC}"
  summary_info "Browse themes: ${DIM}ghostty +list-themes${NC}"
  summary_info "Docs: ${DIM}https://ghostty.org/docs/config${NC}"
}
