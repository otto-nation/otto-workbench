#!/usr/bin/env bats
# Tests for ai_scaffold_gh_token_env (ai/steps.sh), the scaffold ai/setup.sh runs.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"
  ENV_FILE="$FAKE_HOME/.config/task/taskfile.env"
  # file_mode, for the permissions case; the scaffold itself runs in a child bash.
  # shellcheck source=../lib/portable.sh
  . "$REPO_ROOT/lib/portable.sh"
}

teardown() {
  common_teardown
}

_scaffold() {
  run bash -c '
    HOME="$2"
    . "$1/lib/ui.sh"
    . "$1/ai/steps.sh"
    ai_scaffold_gh_token_env "$3"
  ' _ "$REPO_ROOT" "$FAKE_HOME" "$ENV_FILE"
}

@test "an absent file is created with the GH_TOKEN and per-org sections" {
  _scaffold
  [ "$status" -eq 0 ]
  [[ "$output" == *"Created "* ]]
  grep -q '^# GH_TOKEN=github_pat_$' "$ENV_FILE"
  grep -q '^# GH_TOKEN__OTTO_NATION=github_pat_$' "$ENV_FILE"
}

@test "the created file is readable by its owner only" {
  _scaffold
  [ "$status" -eq 0 ]
  run file_mode "$ENV_FILE"
  [ "$output" = "600" ]
}

@test "a file without a GH_TOKEN section gains one and keeps its contents" {
  mkdir -p "$(dirname "$ENV_FILE")"
  printf 'EXISTING=keep-me\n' > "$ENV_FILE"
  _scaffold
  [ "$status" -eq 0 ]
  [[ "$output" == *"Added GH_TOKEN section"* ]]
  [ "$(head -1 "$ENV_FILE")" = "EXISTING=keep-me" ]
  grep -q '^# GH_TOKEN=github_pat_$' "$ENV_FILE"
}

@test "a file that already mentions GH_TOKEN is left byte-identical and silent" {
  mkdir -p "$(dirname "$ENV_FILE")"
  printf 'GH_TOKEN__OTTO_NATION=x\n' > "$ENV_FILE"
  cp "$ENV_FILE" "$TMPDIR/before"
  _scaffold
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  cmp -s "$ENV_FILE" "$TMPDIR/before"
}

@test "a second run changes nothing" {
  _scaffold
  cp "$ENV_FILE" "$TMPDIR/first"
  _scaffold
  [ "$status" -eq 0 ]
  cmp -s "$ENV_FILE" "$TMPDIR/first"
}

@test "GH_TOKEN_SET_RE matches a set token and a per-org token" {
  # shellcheck source=../lib/constants.sh
  . "$REPO_ROOT/lib/constants.sh"
  [[ -n "${GH_TOKEN_SET_RE:-}" ]]
  printf 'GH_TOKEN=x\n' | grep -qE "$GH_TOKEN_SET_RE"
  printf 'GH_TOKEN__OTTO_NATION=x\n' | grep -qE "$GH_TOKEN_SET_RE"
}

@test "GH_TOKEN_SET_RE does not match empty, commented, or adjacent names" {
  # shellcheck source=../lib/constants.sh
  . "$REPO_ROOT/lib/constants.sh"
  [[ -n "${GH_TOKEN_SET_RE:-}" ]]
  run grep -qE "$GH_TOKEN_SET_RE" <<< 'GH_TOKEN='
  [ "$status" -eq 1 ]
  run grep -qE "$GH_TOKEN_SET_RE" <<< '# GH_TOKEN=x'
  [ "$status" -eq 1 ]
  run grep -qE "$GH_TOKEN_SET_RE" <<< 'GH_TOKENX=x'
  [ "$status" -eq 1 ]
}

@test "a pre-existing world-readable file is tightened to 600 with contents unchanged" {
  mkdir -p "$(dirname "$ENV_FILE")"
  printf 'GH_TOKEN=x\n' > "$ENV_FILE"
  chmod 644 "$ENV_FILE"
  cp "$ENV_FILE" "$TMPDIR/before"
  _scaffold
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  cmp -s "$ENV_FILE" "$TMPDIR/before"
  run file_mode "$ENV_FILE"
  [ "$output" = "600" ]
}
