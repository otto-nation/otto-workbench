#!/usr/bin/env bash
# Post-install summary for the packages component.
# Sourced by install.sh after all components run — defines print_packages_summary().
# No top-level execution; safe to source without side effects.

# print_packages_summary — prints next steps for day-to-day Homebrew usage.
# Environment setup (env vars from registries) is handled centrally by lib/summary.sh.
print_packages_summary() {
  summary_section "Homebrew"

  if command -v brew >/dev/null 2>&1; then
    if brew bundle check --file="$CORE_MANIFEST" >/dev/null 2>&1; then
      summary_ok "packages in sync"
    else
      summary_info "some packages not installed — run: ${DIM}otto-workbench install packages${NC}"
    fi
  else
    summary_err "brew not found"
    return
  fi

  summary_info "After installing new packages: ${DIM}task brew:dump${NC} (from the workbench checkout)"
  summary_info "Optional stacks: ${DIM}brew bundle --file=packages/<category>/<stack>.Brewfile${NC}"
}
