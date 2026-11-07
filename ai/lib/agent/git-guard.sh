#!/usr/bin/env bash
# Refuse mutating git subcommands in the fix worktree.
#
# What: PATH shim for fix-shaped agents. Refuses commit/push/reset/… only
#       when the target repo's git dir equals WORKBENCH_GUARDED_GIT_DIR.
# Usage: installed as `git` on PATH by agent.git_guard.guarded; not invoked
#        by operators. Original argv is forwarded to WORKBENCH_REAL_GIT.
# Env vars: WORKBENCH_REAL_GIT, WORKBENCH_GUARDED_GIT_DIR,
#           WORKBENCH_GUARDED_GIT_SUBCOMMANDS
# Side effects: exit 1 on a guarded write, otherwise exec the real git.
set -e

real="${WORKBENCH_REAL_GIT:-}"
guarded="${WORKBENCH_GUARDED_GIT_DIR:-}"
[[ -n "$real" ]] || { echo "git-guard: WORKBENCH_REAL_GIT is unset" >&2; exit 1; }
[[ -n "$guarded" ]] || exec "$real" "$@"

pass() { exec "$real" "$@"; }

# Walk leading global options. Value-taking flags match detect.ts
# GIT_VALUE_FLAGS minus --exec-path (its value form needs =).
i=1
sub=""
while [[ "$i" -le "$#" ]]; do
  word="${!i}"
  case "$word" in
    -C|-c|--git-dir|--work-tree|--namespace|--config-env)
      i=$((i + 2))
      continue
      ;;
    -*)
      i=$((i + 1))
      continue
      ;;
  esac
  sub="$word"
  break
done
[[ -n "$sub" ]] || pass "$@"

rest_start=$((i + 1))

# A non-shell alias rewrites sub to its first word. A ! alias is a residual.
# Follow alias chains to a fixed depth.
for _ in 1 2 3 4 5; do
  [[ " ${WORKBENCH_GUARDED_GIT_SUBCOMMANDS} " != *" $sub "* ]] || break
  alias_val=""
  alias_val=$("$real" "${@:1:i-1}" config --get "alias.$sub" 2>/dev/null) || true
  { [[ -n "$alias_val" && "$alias_val" != '!'* ]] || break; }
  read -r sub _ <<<"$alias_val" || true
done
[[ " ${WORKBENCH_GUARDED_GIT_SUBCOMMANDS} " == *" $sub "* ]] || pass "$@"

next=""
[[ "$rest_start" -le "$#" ]] && next="${!rest_start}"
[[ "$sub" == stash && ( "$next" == list || "$next" == show ) ]] && pass "$@"

j=$rest_start
while [[ "$j" -le "$#" ]]; do
  case "${!j}" in
    --) break ;;
    -m|-F|--message|--file|-C|-c|--reuse-message|--reedit-message)
      j=$((j + 2))
      continue
      ;;
    --dry-run|--help|-h) pass "$@" ;;
    -n) [[ "$sub" != push ]] || pass "$@" ;;
  esac
  j=$((j + 1))
done

target=$("$real" "${@:1:i-1}" rev-parse --absolute-git-dir 2>/dev/null) || pass "$@"
canon=$(cd "$target" && pwd -P) || pass "$@"
[[ "$canon" == "$guarded" ]] || pass "$@"

wt=$("$real" "${@:1:i-1}" rev-parse --show-toplevel 2>/dev/null) || wt="$canon"
echo "git-guard: refusing 'git ${sub}' in ${wt} — the fix pass commits for you; leave your edits in the worktree." >&2
exit 1
