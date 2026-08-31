#!/usr/bin/env bash
# Migration: replace the Homebrew claude-code cask with Claude Code's own
# installer.
#
# The cask's artifact is a bare Mach-O binary, and brew stamps
# com.apple.quarantine on what it downloads. A notarization ticket cannot be
# stapled to a bare Mach-O — stapling needs a bundle, a dmg, or a pkg — so
# Gatekeeper has to look one up online the first time the binary is executed,
# and for a quarantined non-bundle executable it refuses outright: "Apple could
# not verify claude is free of malware", offering Move to Trash and nothing
# else. The binary is genuinely signed by Anthropic PBC and satisfies the
# notarized requirement; the artifact shape is what fails, not the signature.
#
# Stripping the attribute by hand fixes exactly one install, because the
# quarantine arrives with every download and the next upgrade brings it back.
# The native installer's curl download carries no quarantine attribute, so the
# question is never asked, and it self-updates in the background where the cask
# needs a manual `brew upgrade`.
#
# The native install goes in first and has to prove it runs before the cask is
# taken away. A machine left with neither is one that cannot run the tool this
# workbench is built around, and this migration runs unattended.

# _claude_cask_installed — whether Homebrew currently has the claude-code cask.
#
# Only the cask this workbench installed. `claude-code@latest` tracks a
# different release channel and nothing here ever put it there, so removing it
# would undo a choice the operator made by hand.
_claude_cask_installed() {
  command -v brew >/dev/null 2>&1 || return 1
  brew list --cask claude-code >/dev/null 2>&1
}

migration_20260831_claude_native_install() {
  # NOOP rather than deferred. This removes something, so a machine that never
  # had the cask has nothing stale on it, and a cask that appears later is an
  # install whose contents the operator chose.
  _claude_cask_installed || return "$MIGRATION_NOOP"

  if [[ ! -x "$CLAUDE_NATIVE_BIN" ]]; then
    info "Installing Claude Code via its own installer..."
    if ! run_remote_installer "$CLAUDE_INSTALL_URL"; then
      warn "Claude Code's installer failed — leaving the Homebrew cask in place"
      return 1
    fi
  fi

  # The installer can exit 0 having written a launcher that will not start, and
  # the cask is the only other copy on the machine. Ask the replacement to
  # answer for itself before removing the thing it replaces.
  "$CLAUDE_NATIVE_BIN" --version >/dev/null 2>&1 || {
    warn "$CLAUDE_NATIVE_BIN does not run — leaving the Homebrew cask in place"
    return 1
  }

  brew uninstall --cask claude-code >/dev/null 2>&1 || {
    warn "Could not remove the cask — remove it with: brew uninstall --cask claude-code"
    return 1
  }

  success "Claude Code installed at $CLAUDE_NATIVE_BIN; removed the Homebrew cask"
}
