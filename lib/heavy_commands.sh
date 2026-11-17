#!/usr/bin/env bash
# Classify a process as one of the workbench's heavy commands.
#
# The list is the single owner of those names. A process matches by
# executable basename, or as a Python interpreter invoked with `-m tasks`
# as whole arguments:
#
# ```bash
# HEAVY_COMMANDS                  # golangci-lint go gh tasks
# heavy_command_name COMM ARGS    # the heavy name, or nothing
# ```
#
# `heavy_command_name` always returns 0 and prints nothing when the process
# is not heavy, so a caller that wants the raw comm supplies it:
#
# ```bash
# name=$(heavy_command_name "$comm" "$args")
# name=${name:-$comm}
# ```

[[ -n "${_LIB_HEAVY_COMMANDS_SH:-}" ]] && return
_LIB_HEAVY_COMMANDS_SH=1

HEAVY_COMMANDS=(golangci-lint go gh tasks)

# heavy_command_name COMM ARGS — the heavy command name, or nothing.
#
# COMM is treated as a basename. ARGS is the process argument vector as one
# string (ps `args=`). A Python interpreter run with `-m tasks` (after any leading
# options such as `-u` or `-X dev`), `tasks` being a whole argument, classifies as `tasks`; a substring of either
# token does not.
heavy_command_name() {
  local comm="${1##*/}"
  local args="${2:-}"
  local name
  local -a tokens=()
  for name in "${HEAVY_COMMANDS[@]}"; do
    if [[ "$comm" == "$name" ]]; then
      printf '%s' "$name"
      return 0
    fi
  done
  read -r -a tokens <<< "$args"
  [[ "$comm" =~ ^python[0-9.]*$ ]] || return 0
  local i=1
  while (( i < ${#tokens[@]} )); do
    case "${tokens[i]}" in
      -m)
        [[ "${tokens[i + 1]:-}" == "tasks" ]] && printf '%s' "tasks"
        return 0
        ;;
      -X | -W) i=$((i + 2)) ;;
      -*) i=$((i + 1)) ;;
      *) return 0 ;;
    esac
  done
  return 0
}
