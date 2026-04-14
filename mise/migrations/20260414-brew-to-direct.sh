#!/usr/bin/env bash
# Migration: uninstall brew-managed mise now that mise is directly installed.
# Idempotent — no-op if mise is not installed via brew.

migration_20260414_brew_to_direct() {
  command -v brew >/dev/null 2>&1 || return 0

  if brew list --formula mise &>/dev/null; then
    info "Uninstalling brew-managed mise (now directly installed)..."
    brew uninstall mise
    success "Brew mise uninstalled — direct install takes over"
  fi
}
