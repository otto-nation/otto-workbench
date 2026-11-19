#!/usr/bin/env bash
# mise-path-wrapper — strip mise-managed PATH entries, then exec the real mise.
#
# ~/.local/bin/mise is this script after sync. Every shim is a symlink to that
# path, so one strip covers `mise`, `mise activate`, and `python3`/`wt`/`pi`
# shims. mise inserts pinned tools ahead of an existing shims directory, not
# at PATH head; with ~/.local/bin first that leaves a workbench `pi` shadowing
# a repo pin and Maximum's toolchain guard refuses every mise invocation.
#
# ceiling: PATH strip, remove if mise grows a setting that places tool dirs
# at PATH head even when a shims or command-wrappers directory is already on
# PATH. Upgrade trigger: `mise settings ls --all` lists such a knob.
set -euo pipefail

REAL="${MISE_REAL_BIN:-${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}/bin/mise}"
if [[ ! -x "$REAL" ]]; then
  echo "mise wrapper: real binary not at $REAL — re-run otto-workbench sync" >&2
  exit 127
fi

stripped=""
left="${PATH:+$PATH:}"
while [[ -n "$left" ]]; do
  entry="${left%%:*}"
  left="${left#*:}"
  [[ -n "$entry" ]] || continue
  case "$entry" in
    */mise/installs | */mise/installs/* | */mise/shims | */mise/shims/* | */mise/command-wrappers | */mise/command-wrappers/*)
      continue
      ;;
  esac
  stripped="${stripped:+$stripped:}$entry"
done
export PATH="$stripped"
unset stripped left entry
exec -a "$0" "$REAL" "$@"
