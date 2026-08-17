#!/usr/bin/env bats
# Tests for the workbench config reader (lib/config.sh).

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  FAKE_CONFIG="$TMPDIR/config"
  mkdir -p "$FAKE_CONFIG"

  # Point the config root at the sandbox and let constants.sh build the file
  # path from it, so every test below runs against the real join rather than a
  # hand-spelled one.
  export WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"

  # Project scope resolves through `git rev-parse --show-toplevel`, so a test
  # left standing in the real checkout would read this repo's own
  # .workbench.yml the day one lands. Run from the sandbox instead, and cap the
  # upward walk there so no ancestor repo can stand in for the project either.
  export GIT_CEILING_DIRECTORIES="$TMPDIR"
  cd "$TMPDIR" || return 1

  # shellcheck source=../lib/constants.sh
  . "$REPO_ROOT/lib/constants.sh"
  # shellcheck source=../lib/config.sh
  . "$REPO_ROOT/lib/config.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _make_project — a git repo at $TMPDIR/project, cd'd into, holding CONTENT
# as its .workbench.yml. Project scope resolves through git rev-parse, so the
# directory has to actually be a repo.
_make_project() {
  mkdir -p "$TMPDIR/project"
  printf '%s' "$1" > "$TMPDIR/project/.workbench.yml"
  git -C "$TMPDIR/project" init --quiet
  cd "$TMPDIR/project" || return 1
}

@test "wb_config_get reads a nested key" {
  printf 'reuse:\n  level: ultra\n' > "$WORKBENCH_CONFIG_FILE"
  run wb_config_get "reuse.level"
  [ "$status" -eq 0 ]
  [ "$output" = "ultra" ]
}

@test "wb_config_get prints nothing for a missing key" {
  printf 'reuse:\n  level: ultra\n' > "$WORKBENCH_CONFIG_FILE"
  run wb_config_get "review.model"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "wb_config_get prints nothing when there is no config file" {
  run wb_config_get "reuse.level"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "wb_config_get returns the given default for a missing key" {
  run wb_config_get "reuse.level" "full"
  [ "$status" -eq 0 ]
  [ "$output" = "full" ]
}

@test "wb_config_get prefers the project file over the global one" {
  printf 'review:\n  model: sonnet\n' > "$WORKBENCH_CONFIG_FILE"
  _make_project 'review:
  model: opus
'
  run wb_config_get "review.model"
  [ "$status" -eq 0 ]
  [ "$output" = "opus" ]
}

@test "wb_config_get falls back to the global file for a key the project omits" {
  printf 'review:\n  model: sonnet\n  effort: high\n' > "$WORKBENCH_CONFIG_FILE"
  _make_project 'review:
  model: opus
'
  run wb_config_get "review.effort"
  [ "$status" -eq 0 ]
  [ "$output" = "high" ]
}

@test "wb_config_get rejects a key that is not a literal path" {
  printf 'reuse:\n  level: ultra\n' > "$WORKBENCH_CONFIG_FILE"
  run wb_config_get 'reuse.level | ("x")'
  [ "$status" -eq 1 ]
  [[ "$output" == *"invalid config key"* ]]
}

@test "wb_config_get survives a malformed config file" {
  printf 'review:\n  model: [unclosed\n' > "$WORKBENCH_CONFIG_FILE"
  run wb_config_get "review.model" "sonnet"
  [ "$status" -eq 0 ]
  [ "$output" = "sonnet" ]
}

# ─── Seeding (SSOT guard) ────────────────────────────────────────────────────

@test "wb_config_ensure_file creates the file holding just the modeline" {
  run wb_config_ensure_file "$WORKBENCH_CONFIG_FILE"
  [ "$status" -eq 0 ]
  [ "$(cat "$WORKBENCH_CONFIG_FILE")" = "$WORKBENCH_CONFIG_HEADER" ]

  # Comment-only, so a reader still sees an empty config rather than an error.
  run wb_config_get "reuse.level" "full"
  [ "$status" -eq 0 ]
  [ "$output" = "full" ]
}

@test "wb_config_ensure_file leaves an existing file alone" {
  printf 'reuse:\n  level: ultra\n' > "$WORKBENCH_CONFIG_FILE"
  run wb_config_ensure_file "$WORKBENCH_CONFIG_FILE"
  [ "$status" -eq 0 ]
  [ "$(cat "$WORKBENCH_CONFIG_FILE")" = "$(printf 'reuse:\n  level: ultra')" ]
}

@test "wb_config_ensure_file creates the parent directory" {
  local nested="$TMPDIR/missing/config.yml"
  run wb_config_ensure_file "$nested"
  [ "$status" -eq 0 ]
  [ -f "$nested" ]
}

# ─── Cross-validation with the Python owner ──────────────────────────────────
#
# Every name below is spelled in two languages, which CLAUDE.md allows only
# with a test that fails when they drift. Both sides create and read the same
# two files, so a mismatch means one machine's config is written where the
# other never looks, or validated against a schema the other does not serve.

# resolve_python NAME — the value of ai/lib/workbench_config.NAME.
resolve_python() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/ai/lib')
import workbench_config
print(workbench_config.$1, end='')
"
}

@test "the config constants match ai/lib/workbench_config.py" {
  [ "$WORKBENCH_CONFIG_NAME" = "$(resolve_python CONFIG_NAME)" ]
  [ "$WORKBENCH_PROJECT_CONFIG_NAME" = "$(resolve_python PROJECT_CONFIG_NAME)" ]
  [ "$WORKBENCH_CONFIG_SCHEMA_NAME" = "$(resolve_python SCHEMA_PATH)" ]
  [ "$WORKBENCH_REPO_RAW_URL" = "$(resolve_python REPO_RAW_URL)" ]
  [ "$WORKBENCH_CONFIG_SCHEMA_URL" = "$(resolve_python SCHEMA_URL)" ]
  [ "$WORKBENCH_CONFIG_HEADER" = "$(resolve_python CONFIG_HEADER)" ]
}

@test "lib/config.sh refuses to load without the constants" {
  run bash -c ". '$REPO_ROOT/lib/config.sh'"
  [ "$status" -eq 1 ]
  [[ "$output" == *"requires the config constants"* ]]
}
