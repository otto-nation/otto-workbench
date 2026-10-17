#!/usr/bin/env bash
# Run the workbench hook named by WORKBENCH_HOOK, then the repo's own hook
# at REPO_HOOK if it is present and executable. Git invokes a copy of this
# script from a temp hooks dir `pr rebase` builds when the repo's
# core.hooksPath would hide ~/.git-hooks.
#
# Stdin is captured once and replayed to both — post-rewrite is owed the
# same old→new map. The workbench hook's exit status wins when it is
# non-zero (prepare-commit-msg refusing a lossy resolution); otherwise the
# repo hook's status is what git sees.
set -euo pipefail

wb="${WORKBENCH_HOOK:?}"
repo="${REPO_HOOK:?}"

stdin="$(mktemp)"
trap 'rm -f "$stdin"' EXIT
cat > "$stdin"

status=0
if [[ -x "$wb" ]]; then
  "$wb" "$@" < "$stdin" || status=$?
fi
if (( status != 0 )); then
  exit "$status"
fi
if [[ -x "$repo" ]]; then
  "$repo" "$@" < "$stdin" || status=$?
  exit "$status"
fi
exit 0
