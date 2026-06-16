#!/usr/bin/env bash
# Post-install summary for the brew component.
# Sourced by install.sh after all components run — defines print_brew_summary().
# No top-level execution; safe to source without side effects.

# print_brew_summary — prints next steps for day-to-day Homebrew usage.
# Environment setup (env vars from registries) is handled centrally by lib/summary.sh.
print_brew_summary() {
  summary_section "Homebrew"

  if command -v brew >/dev/null 2>&1; then
    if brew bundle check --file="$BREWFILE" >/dev/null 2>&1; then
      summary_ok "packages in sync"
    else
      summary_info "some packages not installed — run: ${DIM}otto-workbench install brew${NC}"
    fi
  else
    summary_err "brew not found"
    return
  fi

  summary_info "After installing new packages: ${DIM}task --global brew:dump${NC}"
  summary_info "Optional stacks: ${DIM}brew bundle --file=brew/<category>/<stack>.Brewfile${NC}"
}
