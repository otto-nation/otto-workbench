#!/usr/bin/env bash
# Reading a single variable's value out of ~/.env.local.
#
# Both harness sync paths (ai/claude/steps.sh, ai/pi/steps.sh) resolve model
# config the same way: grep the file directly for `export VAR=` rather than the
# ambient environment, because the sync that matters most cannot see one —
# maintenance/bin/otto-workbench-maintenance runs `otto-workbench sync` from
# launchd with nothing but PATH set, so an environment-derived value would be
# blanked on every unattended run and restored by hand the next time someone
# synced from a terminal.
#
# It has no dependencies beyond ENV_LOCAL_FILE (from lib/constants.sh), so a
# caller that has not loaded the facade can source it on its own:
#
# ```bash
# read_env_local_var AI_MODEL
# ```

[[ -n "${_LIB_ENV_SH:-}" ]] && return
_LIB_ENV_SH=1

# read_env_local_var VAR — prints the value ~/.env.local exports for VAR, or
# nothing if VAR is not exported there.
#
# A shell file quotes what needs quoting; the callers here want the bare value.
# Only a matched pair comes off — a lone quote is part of a line nothing here
# can read confidently, and it travels intact rather than half-stripped.
read_env_local_var() {
  local var="$1" line value
  line=$(grep -m1 "^export ${var}=" "$ENV_LOCAL_FILE" 2>/dev/null) || return 0
  value=${line#*=}
  if [[ "$value" == \"*\" || "$value" == \'*\' ]]; then
    value=${value:1:${#value}-2}
  fi
  [[ -n "$value" ]] && printf '%s' "$value"
  return 0
}
