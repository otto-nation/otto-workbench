#!/usr/bin/env bats
# Tests for ai_scaffold_gh_token_env (ai/steps.sh) — the token-file scaffold
# ai/setup.sh runs in place of the retired `task --global ai:setup`.
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
  grep -q '^# GH_TOKEN=github_pat_$' "$ENV_FILE"
  grep -q '^# GH_TOKEN__OTTO_NATION=github_pat_$' "$ENV_FILE"
}

@test "the created file names no retired setting" {
  _scaffold
  [ "$status" -eq 0 ]
  run grep -E 'AI_COMMAND|ANTHROPIC_API_KEY|task pr:|ai:setup' "$ENV_FILE"
  [ "$status" -eq 1 ]
}

@test "the created file is readable by its owner only" {
  _scaffold
  [ "$status" -eq 0 ]
  run file_mode "$ENV_FILE"
  [ "$output" = "600" ]
}

@test "a file without a GH_TOKEN section gains one and keeps its contents" {
  mkdir -p "$(dirname "$ENV_FILE")"
  printf 'AI_COMMAND=claude -p\n' > "$ENV_FILE"
  _scaffold
  [ "$status" -eq 0 ]
  [ "$(head -1 "$ENV_FILE")" = "AI_COMMAND=claude -p" ]
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
