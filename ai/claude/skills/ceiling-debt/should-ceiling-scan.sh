#!/usr/bin/env bash
# should-ceiling-scan.sh — checks whether ceiling ledger regeneration is needed.
# Returns 0 (true) if the current repo has ceiling: markers.
# Returns 1 (false) otherwise.
# Used by the Stop hook to auto-regenerate .claude/ceiling-debt.md.

set -e

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 1
ceiling_scan="$(dirname "$(readlink "${BASH_SOURCE[0]}" 2>/dev/null || echo "${BASH_SOURCE[0]}")")/../../bin/ceiling-scan"

summary=$(python3 "$ceiling_scan" --summary-only "$repo_root" 2>/dev/null) || exit 1

if [[ "$summary" == "0 ceiling marker(s)"* ]]; then
  exit 1
fi

exit 0
