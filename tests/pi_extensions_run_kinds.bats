#!/usr/bin/env bats
# Tests for the shared kind table in run-dir.ts (batch vs target session ids).
# select-on: ai/pi/extensions/_shared/run-dir.ts

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  unset WORKBENCH_RUN_DIR
  unset WORKBENCH_TARGET_DIR
  unset WORKBENCH_SESSION_ID
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  SHARED="$REPO_ROOT/ai/pi/extensions/_shared"
}

teardown() {
  common_teardown
}

# _eval EXPR [ARG...] — prints JSON.stringify(EXPR) with run-dir.ts as m.
_eval() {
  local expr="$1"
  shift
  run node --input-type=module -e "
    const m = await import('$SHARED/run-dir.ts');
    const args = process.argv.slice(1);
    process.stdout.write(JSON.stringify($expr));
  " -- "$@"
}

@test "run-kinds: batch id match" {
  _eval 'm.sessionOf({WORKBENCH_RUN_DIR: args[0]}, args[1])' \
    "/tmp/batch/20261114T120000-ab12" "batch-20261114T120000-ab12"
  [ "$status" -eq 0 ]
  [ "$output" = '{"kind":"batch","dir":"/tmp/batch/20261114T120000-ab12"}' ]
}

@test "run-kinds: target id match requires env id equality" {
  _eval 'm.sessionOf({WORKBENCH_TARGET_DIR: args[0], WORKBENCH_SESSION_ID: args[1]}, args[1])' \
    "/tmp/targets/deadbeefcafebabe" "target-deadbeefcafebabe"
  [ "$status" -eq 0 ]
  [ "$output" = '{"kind":"target","dir":"/tmp/targets/deadbeefcafebabe"}' ]
}

@test "run-kinds: a target env with a wrong id is null" {
  _eval 'm.sessionOf({WORKBENCH_TARGET_DIR: args[0], WORKBENCH_SESSION_ID: args[1]}, args[2])' \
    "/tmp/targets/deadbeefcafebabe" "target-deadbeefcafebabe" "target-ffffffffffffffff"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "run-kinds: a blank dir is null" {
  _eval 'm.sessionOf({WORKBENCH_RUN_DIR: "  ", WORKBENCH_TARGET_DIR: "", WORKBENCH_SESSION_ID: args[0]}, args[0])' \
    "target-deadbeefcafebabe"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "run-kinds: the batch env with the target id is null" {
  _eval 'm.sessionOf({WORKBENCH_RUN_DIR: args[0]}, args[1])' \
    "/tmp/batch/20261114T120000-ab12" "target-deadbeefcafebabe"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}

@test "run-kinds: both envs set match batch by id" {
  _eval 'm.sessionOf({WORKBENCH_RUN_DIR: args[0], WORKBENCH_TARGET_DIR: args[1], WORKBENCH_SESSION_ID: args[2]}, args[3])' \
    "/tmp/batch/20261114T120000-ab12" "/tmp/targets/deadbeefcafebabe" \
    "target-deadbeefcafebabe" "batch-20261114T120000-ab12"
  [ "$status" -eq 0 ]
  [ "$output" = '{"kind":"batch","dir":"/tmp/batch/20261114T120000-ab12"}' ]
}

@test "run-kinds: both envs set match target by id" {
  _eval 'm.sessionOf({WORKBENCH_RUN_DIR: args[0], WORKBENCH_TARGET_DIR: args[1], WORKBENCH_SESSION_ID: args[2]}, args[2])' \
    "/tmp/batch/20261114T120000-ab12" "/tmp/targets/deadbeefcafebabe" \
    "target-deadbeefcafebabe"
  [ "$status" -eq 0 ]
  [ "$output" = '{"kind":"target","dir":"/tmp/targets/deadbeefcafebabe"}' ]
}

@test "run-kinds: a target id missing the target- prefix is null" {
  _eval 'm.sessionOf({WORKBENCH_TARGET_DIR: args[0], WORKBENCH_SESSION_ID: args[1]}, args[1])' \
    "/tmp/targets/deadbeefcafebabe" "deadbeefcafebabe"
  [ "$status" -eq 0 ]
  [ "$output" = "null" ]
}
