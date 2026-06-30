#!/usr/bin/env bash
# should-ceiling-scan.sh — checks whether ceiling ledger regeneration is needed.
# Returns 0 (true) if the current repo has ceiling: markers.
# Returns 1 (false) otherwise.
# Used by the Stop hook to auto-regenerate .claude/ceiling-debt.md.
#
# Usage: should-ceiling-scan.sh [repo_root]
#   repo_root — optional, avoids redundant git rev-parse in caller

set -e

repo_root="${1:-$(git rev-parse --show-toplevel 2>/dev/null)}" || exit 1

python3 "$HOME/.claude/bin/ceiling-scan" --check "$repo_root"
