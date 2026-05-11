#!/usr/bin/env bash
# Bootstrap wrapper — delegates to otto-workbench install.
#
# Usage: ./install.sh [--all] [COMPONENT ...]
#
# This script exists for backward compatibility and as a curl-able entry point.
# The real implementation lives in bin/otto-workbench.
#
# Re-running is safe — existing symlinks are updated silently; real files prompt before overwrite.

set -e

DOTFILES_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Verify modern bash (4.3+ for namerefs, associative arrays, etc.).
# macOS ships bash 3.2 at /bin/bash — Homebrew's bash is required.
if [[ "${BASH_VERSINFO[0]}" -lt 4 || ( "${BASH_VERSINFO[0]}" -eq 4 && "${BASH_VERSINFO[1]}" -lt 3 ) ]]; then
  echo "✗ bash 4.3+ is required (found ${BASH_VERSION})" >&2
  echo "  Install modern bash: brew install bash" >&2
  echo "  Then re-run: bash install.sh" >&2
  exit 1
fi

exec "$DOTFILES_DIR/bin/otto-workbench" install "$@"
