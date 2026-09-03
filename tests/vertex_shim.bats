#!/usr/bin/env bats
# Tests for zsh/config.d/tools/vertex.zsh — mirrors ANTHROPIC_VERTEX_PROJECT_ID
# into GOOGLE_CLOUD_PROJECT, which is the only name Pi's vertex-claude extension
# resolves a project from.
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
    printf '%s\n' 'typeset -ga precmd_functions'
    printf '%s\n' "SHIM=${SHIM}"
    printf '%s\n' 'first_prompt() { local h; for h in $precmd_functions; do $h; done }'
    printf '%s\n' "$1"
  } > "$PROBE"
  zsh -f "$PROBE"
}

@test "mirrors a project id the layers can already see" {
  run _probe '
    export ANTHROPIC_VERTEX_PROJECT_ID=proj-early
    source $SHIM
    print -r -- "${GOOGLE_CLOUD_PROJECT:-UNSET}"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "proj-early" ]
}

@test "registers no hook when it resolved on the spot" {
  run _probe '
    export ANTHROPIC_VERTEX_PROJECT_ID=proj-early
    source $SHIM
    print -r -- "${#precmd_functions}"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
}

@test "mirrors at the first prompt a project id exported after the layers ran" {
  # The bug this file exists for: ~/.zshrc sources the loader near the top and
  # exports below it, so the shim used to run before the value it needed was set
  # and left GOOGLE_CLOUD_PROJECT unset for the whole session.
  run _probe '
    source $SHIM
    export ANTHROPIC_VERTEX_PROJECT_ID=proj-late
    first_prompt
    print -r -- "${GOOGLE_CLOUD_PROJECT:-UNSET}"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "proj-late" ]
}

@test "the retry hook retires itself once it has run" {
  run _probe '
    source $SHIM
    export ANTHROPIC_VERTEX_PROJECT_ID=proj-late
    first_prompt
    print -r -- "${#precmd_functions}"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
}

@test "the hook retires even when no project id ever arrives" {
  # Nothing to mirror is the common case on a machine that does not use Vertex.
  # A hook that stayed registered would run at every prompt for the session.
  run _probe '
    source $SHIM
    first_prompt
    print -r -- "${#precmd_functions}"
    print -r -- "${GOOGLE_CLOUD_PROJECT:-UNSET}"
  '
  [ "$status" -eq 0 ]
  [ "${lines[0]}" = "0" ]
  [ "${lines[1]}" = "UNSET" ]
}

@test "leaves none of its helpers defined" {
  run _probe '
    source $SHIM
    export ANTHROPIC_VERTEX_PROJECT_ID=proj-late
    first_prompt
    print -r -- "${(k)functions[(I)_wb_vertex_*]}"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "" ]
}

@test "leaves a GOOGLE_CLOUD_PROJECT the operator set alone" {
  run _probe '
    export GOOGLE_CLOUD_PROJECT=someone-elses
    export ANTHROPIC_VERTEX_PROJECT_ID=proj-early
    source $SHIM
    first_prompt
    print -r -- "$GOOGLE_CLOUD_PROJECT"
  '
  [ "$status" -eq 0 ]
  [ "$output" = "someone-elses" ]
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
