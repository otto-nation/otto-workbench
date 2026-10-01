#!/usr/bin/env bash
# Runs ai/lib/rebase/replay_audit.py for the global commit hooks.
#
# Two hooks reach the same audit — `prepare-commit-msg` refuses a conflict
# resolution that throws away changes, `post-rewrite` reports commits a rebase
# dropped with their changes — and this is the one place that knows how to
# start it. Sourced on its own, with no dependencies, for the reason
# lib/git_remote.sh gives: a hook that runs in every repository on the machine
# must not load the whole workbench to answer one question.
#
# ```bash
# . "$_workbench/lib/replay_audit.sh"
# replay_audit "$_workbench" commit || exit 1
# ```

# The audit's own refusal code. Anything else non-zero is the audit failing to
# run — an interpreter too old for the library, a moved checkout — and is
# reported and waved through, never mistaken for a refusal.
REPLAY_AUDIT_REFUSED=10

# replay_audit WORKBENCH SUBCOMMAND [ARGS...] — returns 1 only on a refusal.
#
# `python3 -I` because the hook's cwd is somebody else's repository: isolated
# mode adds neither that directory nor the script's to sys.path, so a
# `types.py` at the repo root — or ai/lib/rebase's own `inspect.py` — cannot
# shadow the standard library. ai/lib is then put on the path by hand.
replay_audit() {
  local workbench="$1"
  shift
  local lib="$workbench/ai/lib"
  [[ -f "$lib/rebase/replay_audit.py" ]] || return 0

  local status=0
  python3 -I -c \
    'import sys; sys.path.insert(0, sys.argv[1]); from rebase.replay_audit import main; sys.exit(main(sys.argv[2:]))' \
    "$lib" "$@" || status=$?

  if (( status == REPLAY_AUDIT_REFUSED )); then
    return 1
  fi
  if (( status != 0 )); then
    echo "⚠ replay audit could not run (exit $status) — not checked for discarded changes" >&2
  fi
  return 0
}
