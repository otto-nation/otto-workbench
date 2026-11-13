#!/usr/bin/env bash
# Run a captured command with a progress heartbeat and a hard time bound.
#
# One owner for "run this where nobody can see it, and do not let it hang".
# The pre-push hook captures its validators' output (so a pass prints one line)
# and asks gh a question that must not delay a push; both used to hand-roll the
# same process-group poll loop, and only one had a heartbeat. `timeout` is GNU,
# and this is a BSD userland, so the deadline is kept by polling.
#
# The child runs in a process group of its own (`set -m`), so the deadline
# reaches everything it spawned rather than reparenting its descendants to init.
# A step killed this way had nothing in flight on the network — it is a local
# check — which is what makes a hard bound safe here and not on `git push`.
#
# Return 124 on timeout is also what a command that itself exits 124 produces.
# A caller who needs to tell the two apart checks OUT.timeout, which this
# writes only when the deadline fires.

[[ -n "${_LIB_RUN_BOUNDED_SH:-}" ]] && return
_LIB_RUN_BOUNDED_SH=1

# bounded_seconds NAME DEFAULT — whole seconds from env var NAME: unset or empty is
# DEFAULT, a decimal is truncated (a positive fraction under one becomes 1), and
# anything else warns on stderr and is DEFAULT, so a typo never removes a bound.
bounded_seconds() {
  local name="$1" default="$2" raw="${!1:-}" whole
  if [[ -z "$raw" ]]; then
    echo "$default"
    return 0
  fi
  if [[ ! "$raw" =~ ^([0-9]+)(\.([0-9]*))?$ ]]; then
    echo "warning: $name must be a number of seconds, got '$raw'; using $default" >&2
    echo "$default"
    return 0
  fi
  whole=$((10#${BASH_REMATCH[1]}))
  if (( whole == 0 )) && [[ "${BASH_REMATCH[3]:-}" =~ [1-9] ]]; then
    whole=1
  fi
  echo "$whole"
}

# run_bounded DEADLINE HEARTBEAT LABEL OUT ERR CMD... — run CMD with a heartbeat and a hard bound; 124 on timeout. Writes OUT.timeout when the deadline fires so a caller can tell that from a command that itself exits 124.
run_bounded() {
  local deadline="$1" heartbeat="$2" label="$3" out="$4" errf="$5" pid waited=0 first_beat=1
  shift 5
  # Whole seconds only. A caller reading these from the environment normalizes
  # them first (`bounded_seconds`), so a bad value here is a caller bug.
  if [[ ! "$deadline" =~ ^[0-9]+$ || ! "$heartbeat" =~ ^[0-9]+$ ]]; then
    echo "run_bounded: DEADLINE and HEARTBEAT must be whole seconds, got '$deadline' '$heartbeat'" >&2
    return 2
  fi
  set -m
  if [[ "$out" == "$errf" ]]; then
    "$@" > "$out" 2>&1 &
  else
    "$@" > "$out" 2> "$errf" &
  fi
  pid=$!
  set +m
  # Ctrl-C reaches only the hook, not this separate group: take the step down too.
  trap 'kill -TERM -- -"$pid" 2> /dev/null || kill -TERM "$pid" 2> /dev/null; wait "$pid" 2> /dev/null; trap - INT TERM; return 130' INT TERM
  while kill -0 "$pid" 2> /dev/null; do
    if (( deadline > 0 && waited >= deadline )); then
      kill -TERM -- -"$pid" 2> /dev/null || kill -TERM "$pid" 2> /dev/null
      wait "$pid" 2> /dev/null || true
      trap - INT TERM
      : > "$out.timeout"
      return 124
    fi
    sleep 1
    waited=$((waited + 1))
    if (( heartbeat > 0 && waited % heartbeat == 0 )) && kill -0 "$pid" 2> /dev/null; then
      # First beat starts its own line so it does not glue onto a caller's
      # "→ label... " prefix; later beats already follow a newline.
      if (( first_beat )); then
        printf '\n' >&2
        first_beat=0
      fi
      printf '⏱ %s still running — %dm%02ds\n' "$label" $((waited / 60)) $((waited % 60)) >&2
    fi
  done
  local status=0
  wait "$pid" || status=$?
  trap - INT TERM
  return "$status"
}
