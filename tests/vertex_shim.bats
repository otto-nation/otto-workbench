#!/usr/bin/env bats
# Tests for zsh/config.d/tools/vertex.zsh — fills in each Vertex variable under
# the name its consumer reads it by: GOOGLE_CLOUD_PROJECT into
# ANTHROPIC_VERTEX_PROJECT_ID for vertex_quota.py and the Claude Code CLI, and
# CLOUD_ML_REGION into GOOGLE_CLOUD_LOCATION for Pi's built-in google-vertex
# provider, which registers no models at all without it.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  command -v zsh >/dev/null 2>&1 || skip "zsh not available"
  SHIM="$REPO_ROOT/zsh/config.d/tools/vertex.zsh"
  PROBE="$BATS_TEST_TMPDIR/probe.zsh"
}

teardown() {
  common_teardown
}

# _probe BODY — runs BODY in a pristine zsh with the shim's inputs unset. The
# body reports through `print`, and calling every precmd hook by hand is what
# stands in for the first prompt a login shell would draw.
_probe() {
  {
    printf '%s\n' 'emulate -L zsh'
    printf '%s\n' 'unset GOOGLE_CLOUD_PROJECT ANTHROPIC_VERTEX_PROJECT_ID'
    printf '%s\n' 'unset CLOUD_ML_REGION GOOGLE_CLOUD_LOCATION'
    printf '%s\n' 'typeset -ga precmd_functions'
    printf '%s\n' "SHIM=${SHIM}"
    printf '%s\n' 'first_prompt() { local h; for h in $precmd_functions; do $h; done }'
    printf '%s\n' "$1"
  } > "$PROBE"
  zsh -f "$PROBE"
}

@test "mirrors both values when the layers can already see them" {
  run _probe '
    export GOOGLE_CLOUD_PROJECT=proj-early
    export CLOUD_ML_REGION=global
    source $SHIM
    print -r -- "${ANTHROPIC_VERTEX_PROJECT_ID:-UNSET}"
    print -r -- "${GOOGLE_CLOUD_LOCATION:-UNSET}"
  '
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "proj-early" ]
  [ "${lines[1]}" = "global" ]
}

@test "registers no hook when both pairs resolved on the spot" {
  run _probe '
    export GOOGLE_CLOUD_PROJECT=proj-early
    export CLOUD_ML_REGION=global
    source $SHIM
    print -r -- "${#precmd_functions}"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
}

@test "retries at the first prompt when only one pair resolved" {
  # A partial pass is still a pass owed: the second source can arrive from
  # ~/.zshrc's own block the same way the first can.
  run _probe '
    export GOOGLE_CLOUD_PROJECT=proj-early
    source $SHIM
    print -r -- "${#precmd_functions}"
    export CLOUD_ML_REGION=global
    first_prompt
    print -r -- "${GOOGLE_CLOUD_LOCATION:-UNSET}"
  '
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "1" ]
  [ "${lines[1]}" = "global" ]
}

@test "mirrors at the first prompt values exported after the layers ran" {
  # The bug this file exists for: ~/.zshrc sources the loader near the top and
  # exports below it, so the shim used to run before the values it needed were
  # set and left the targets unset for the whole session.
  run _probe '
    source $SHIM
    export GOOGLE_CLOUD_PROJECT=proj-late
    export CLOUD_ML_REGION=us-east5
    first_prompt
    print -r -- "${ANTHROPIC_VERTEX_PROJECT_ID:-UNSET}"
    print -r -- "${GOOGLE_CLOUD_LOCATION:-UNSET}"
  '
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "proj-late" ]
  [ "${lines[1]}" = "us-east5" ]
}

@test "the retry hook retires itself once it has run" {
  run _probe '
    source $SHIM
    export GOOGLE_CLOUD_PROJECT=proj-late
    export CLOUD_ML_REGION=global
    first_prompt
    print -r -- "${#precmd_functions}"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
}

@test "the hook retires even when nothing ever arrives" {
  # Nothing to mirror is the common case on a machine that does not use Vertex.
  # A hook that stayed registered would run at every prompt for the session.
  run _probe '
    source $SHIM
    first_prompt
    print -r -- "${#precmd_functions}"
    print -r -- "${ANTHROPIC_VERTEX_PROJECT_ID:-UNSET}"
    print -r -- "${GOOGLE_CLOUD_LOCATION:-UNSET}"
  '
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "0" ]
  [ "${lines[1]}" = "UNSET" ]
  [ "${lines[2]}" = "UNSET" ]
}

@test "leaves none of its helpers or its table defined" {
  run _probe '
    source $SHIM
    export GOOGLE_CLOUD_PROJECT=proj-late
    export CLOUD_ML_REGION=global
    first_prompt
    print -r -- "fns=${(k)functions[(I)_wb_vertex_*]}"
    print -r -- "table=${_wb_vertex_pairs:-gone}"
  '
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "fns=" ]
  [ "${lines[1]}" = "table=gone" ]
}

@test "leaves a target the operator set alone" {
  run _probe '
    export ANTHROPIC_VERTEX_PROJECT_ID=someone-elses
    export GOOGLE_CLOUD_PROJECT=proj-early
    export CLOUD_ML_REGION=global
    source $SHIM
    first_prompt
    print -r -- "$ANTHROPIC_VERTEX_PROJECT_ID"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "someone-elses" ]
}

@test "lets the two regions differ" {
  # CLOUD_ML_REGION is where the Anthropic models are provisioned;
  # GOOGLE_CLOUD_LOCATION is where Google's own are served. A machine with Claude
  # on a regional endpoint and Gemini on the global one is an ordinary
  # configuration, and the mirror must not flatten it.
  run _probe '
    export CLOUD_ML_REGION=us-east5
    export GOOGLE_CLOUD_LOCATION=global
    source $SHIM
    first_prompt
    print -r -- "${CLOUD_ML_REGION}/${GOOGLE_CLOUD_LOCATION}"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "us-east5/global" ]
}

@test "sourcing twice registers the retry hook once" {
  # The loader is sourced again by every `exec zsh`, and a sub-shell that sources
  # the layers itself is ordinary. A second registration would double the work
  # and leave a hook behind after the first one retired.
  run _probe '
    source $SHIM
    source $SHIM
    print -r -- "${#precmd_functions}"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "1" ]
}
