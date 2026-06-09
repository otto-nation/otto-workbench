#!/usr/bin/env bash
# Command registration helpers — SSOT for subcommand documentation.
# Bash-only (uses arrays and namerefs).
#
# Scripts declare a COMMANDS array of alternating "usage_form" "description" pairs.
# Nested dispatchers use COMMANDS_PARENT (uppercased) arrays.
# Handler functions follow cmd_NAME (top-level) or cmd_PARENT_CHILD (nested) naming.
#
# Functions: commands_usage

[[ -n "${_LIB_COMMANDS_SH:-}" ]] && return
_LIB_COMMANDS_SH=1

# commands_usage [ARRAY_NAME]
# Print formatted command list from a COMMANDS-style array.
# Auto-aligns description column based on the longest usage form.
commands_usage() {
  local -n __cmds="${1:-COMMANDS}"
  local i max_len=0 len

  for ((i = 0; i < ${#__cmds[@]}; i += 2)); do
    len=${#__cmds[i]}
    (( len > max_len )) && max_len=$len
  done

  local pad=$(( max_len + 2 ))
  for ((i = 0; i < ${#__cmds[@]}; i += 2)); do
    printf "  %-${pad}s %s\n" "${__cmds[i]}" "${__cmds[i+1]}"
  done
}
