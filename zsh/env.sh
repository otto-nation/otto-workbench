# shellcheck shell=sh
# env.sh — the environment a workbench tool needs, for any caller and any shell.
#
# The single owner of which directories user-installed tools live in and how
# ~/.env.local is loaded. Every caller that does not start from an interactive
# zsh reads it rather than keeping its own PATH line:
#
#   loader.zsh                    the interactive shell, before any config layer
#   lib/setup.sh ensure_tool_path sync, from whatever launched it
#   otto-workbench-maintenance    launchd / systemd, and the agents it spawns
#   herdr-app's remote preamble   commands herdr-core starts on another machine
#
# POSIX sh, because dash runs herdr's remote preamble on Linux. It defines
# functions and runs nothing when sourced, so sourcing it never has a side
# effect of its own.
#
# Copied (never symlinked) to ~/.config/zsh/env.sh by the zsh step, for the same
# reason as loader.zsh: it must survive the repo moving and be readable from
# sandboxed apps. The destination is HOME-fixed because the herdr preamble has no
# way to resolve the workbench's XDG roots on the far machine.

# workbench_path — puts the directories user-installed tools live in at the head
# of PATH, in the precedence an interactive shell resolves them: ~/.local/bin
# (the workbench's own `pr` must beat coreutils'), Pi's bin, mise's shims,
# Homebrew, then the inherited PATH. Directories that do not exist are skipped.
#
# A listed directory already on PATH is moved rather than duplicated, so the
# order holds whatever the caller started from. An interactive shell is not
# changed by this: `brew shellenv` and `mise activate` run later in the tools
# layer and put their own directories first either way.
#
# PATH is split by parameter expansion rather than IFS because zsh, which runs
# the loader, does not word-split an unquoted parameter. Empty entries (an
# implicit current directory) are dropped.
workbench_path() {
  # Lowest precedence first: each prepend lands ahead of the previous one.
  for _wb_dir in \
    /home/linuxbrew/.linuxbrew/bin \
    /opt/homebrew/bin \
    "${MISE_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/mise}/shims" \
    "$HOME/.pi/agent/bin" \
    "$HOME/.local/bin"; do
    [ -d "$_wb_dir" ] || continue
    _wb_rest=""
    _wb_left="${PATH:+$PATH:}"
    while [ -n "$_wb_left" ]; do
      _wb_entry="${_wb_left%%:*}"
      _wb_left="${_wb_left#*:}"
      [ -n "$_wb_entry" ] || continue
      [ "$_wb_entry" = "$_wb_dir" ] && continue
      _wb_rest="${_wb_rest:+$_wb_rest:}$_wb_entry"
    done
    PATH="$_wb_dir${_wb_rest:+:$_wb_rest}"
  done
  unset _wb_dir _wb_rest _wb_left _wb_entry
  export PATH
}

# workbench_env — workbench_path, then ~/.env.local with every assignment
# exported, so a child process sees what an interactive shell would. Always
# returns 0: a caller's environment is degraded by a bad ~/.env.local, never
# ended by it.
#
# An interactive shell sources the file directly, so a mistake in it prints
# where it is. A non-interactive one sources it only after a subshell has proved
# it loads: sh exits on a failing `.`, which would end the caller before it ran
# anything — a remote preamble before its `exec`, maintenance before its sync.
# The proof runs the file twice, so it is not used where a person is watching.
#
# A caller under `set -e` is safe too: errexit is lifted around the source and
# restored after, so a command in ~/.env.local that returns non-zero does not end
# the caller. Calling it as `workbench_env || true` is not enough on its own —
# dash and bash 3.2 still exit on a failing command inside a `.` script there.
workbench_env() {
  workbench_path
  [ -r "$HOME/.env.local" ] || return 0
  case "$-" in
    *a*) _wb_allexport=1 ;;
    *) _wb_allexport="" ;;
  esac
  case "$-" in
    *e*) _wb_errexit=1; set +e ;;
    *) _wb_errexit="" ;;
  esac
  case "$-" in
    *i*)
      set -a
      # shellcheck disable=SC1091  # the operator's file, not ours to lint
      . "$HOME/.env.local"
      ;;
    *)
      if (. "$HOME/.env.local") >/dev/null 2>&1; then
        set -a
        # shellcheck disable=SC1091
        . "$HOME/.env.local" >/dev/null 2>&1
      else
        echo "workbench: $HOME/.env.local failed to load (a syntax error, or its last command returned non-zero) — skipped" >&2
      fi
      ;;
  esac
  [ -n "$_wb_allexport" ] || set +a
  [ -z "$_wb_errexit" ] || set -e
  unset _wb_allexport _wb_errexit
  return 0
}
