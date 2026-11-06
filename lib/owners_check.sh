#!/usr/bin/env bash
# Runs ai/lib/cli/owners_check.py for the global commit-msg and pre-push hooks.
#
# Sourced on its own, with no dependencies, for the reason lib/git_remote.sh
# gives: a hook that runs in every repository on the machine must not load
# the whole workbench to answer one question.
#
# ```bash
# . "$_workbench/lib/owners_check.sh"
# owners_check_hook "$_workbench" --message-file "$1" --cwd "$PWD" || exit 1
# ```

[[ -n "${_LIB_OWNERS_CHECK_SH:-}" ]] && return
_LIB_OWNERS_CHECK_SH=1

# The check's own refusal code. Equals REFUSED in cli/owners_check.py.
# Anything else non-zero is the check failing to run — an interpreter too
# old for the library, a moved checkout — and is reported and waved through,
# never mistaken for a refusal.
#
# A hook, unlike a guard, fails open: it runs in every repository on the
# machine, so a check that could not run must not refuse the commit or push.
readonly OWNERS_CHECK_REFUSED=2

# owners_check_hook WORKBENCH ARGS... — runs owners-check with ARGS; returns 1
# only on a refusal. Stdin is passed through to python (pre-push redirects
# the captured ref lines).
#
# `python3 -I` because the hook's cwd is somebody else's repository: isolated
# mode adds neither that directory nor the script's to sys.path, so a
# `types.py` at the repo root cannot shadow the standard library. ai/lib is
# then put on the path by hand.
owners_check_hook() {
  local workbench="$1"
  shift
  local lib="$workbench/ai/lib"
  [[ -f "$lib/cli/owners_check.py" ]] || return 0

  local status=0
  python3 -I -c \
    'import sys; sys.path.insert(0, sys.argv[1]); from cli.owners_check import main; sys.exit(main(sys.argv[2:]))' \
    "$lib" "$@" || status=$?

  if (( status == OWNERS_CHECK_REFUSED )); then
    return 1
  fi
  if (( status != 0 )); then
    echo "⚠ owner check could not run (exit $status) — not checked for cross-group references" >&2
  fi
  return 0
}
