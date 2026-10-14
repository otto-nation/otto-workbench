#!/usr/bin/env bash
# Post-install summary for the packages component.
# Sourced by install.sh after all components run — defines print_packages_summary().
# No top-level execution; safe to source without side effects.

# _pkg_summary_mise — prints the path of a mise binary, looking on PATH and then
# in the locations a bootstrap writes (so a parent process whose PATH was not
# updated still sees it).
_pkg_summary_mise() {
  if command -v mise >/dev/null 2>&1; then
    command -v mise
    return 0
  fi
  if [[ -x "${LOCAL_BIN_DIR:-}/mise" ]]; then
    echo "$LOCAL_BIN_DIR/mise"
    return 0
  fi
  if [[ -x "${MISE_SHIMS_DIR:-}/mise" ]]; then
    echo "$MISE_SHIMS_DIR/mise"
    return 0
  fi
  return 1
}

# print_packages_summary — prints next steps for day-to-day package usage.
# Environment setup (env vars from registries) is handled centrally by lib/summary.sh.
print_packages_summary() {
  if command -v brew >/dev/null 2>&1; then
    summary_section "Homebrew"
    if brew bundle check --file="$CORE_MANIFEST" >/dev/null 2>&1; then
      summary_ok "packages in sync"
    else
      summary_info "some packages not installed — run: ${DIM}otto-workbench install packages${NC}"
    fi
    summary_info "After installing new packages: ${DIM}task brew:dump${NC} (from the workbench checkout)"
    summary_info "Optional stacks: ${DIM}brew bundle --file=packages/<category>/<stack>.Brewfile${NC}"
    return
  fi

  summary_section "Packages"
  if _pkg_summary_mise >/dev/null; then
    summary_info "managed via mise"
    summary_info "Re-run: ${DIM}otto-workbench install packages${NC}"
    return
  fi
  summary_err "neither Homebrew nor mise found"
}
