#!/usr/bin/env bash
# should-ceiling-scan.sh — checks whether ceiling ledger regeneration is needed.
# Returns 0 (true) if the current repo has ceiling: markers.
# Returns 1 (false) otherwise.
# Used by the Stop hook to auto-regenerate .claude/ceiling-debt.md.

set -e

repo_root=$(git rev-parse --show-toplevel 2>/dev/null) || exit 1

grep -rq 'ceiling:' "$repo_root" \
  --include='*.py' --include='*.go' --include='*.js' --include='*.ts' \
  --include='*.sh' --include='*.rb' --include='*.rs' --include='*.java' \
  --include='*.sql' --include='*.yml' --include='*.yaml' \
  --exclude-dir='.git' --exclude-dir='node_modules' --exclude-dir='vendor' \
  --exclude-dir='build' --exclude-dir='dist' --exclude-dir='ignore' \
  2>/dev/null || exit 1
