#!/usr/bin/env bats
# Tests for herdr-app batch busy-PR helper against pr batch status --full JSON.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js ai/lib/batch/model.py

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# $1 = status JSON, $2 = repo. Prints JSON array of busy PR numbers.
_busy() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch.js');
    process.stdout.write(JSON.stringify([...m.busyPrs(JSON.parse(process.argv[1]), process.argv[2])]));
  " -- "$1" "$2"
}

@test "each busy state is busy" {
  _busy '{"run":{"items":[{"key":"o/r#1","status":"queued"},{"key":"o/r#2","status":"waiting_admission"},{"key":"o/r#3","status":"running"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[1,2,3]' ]
}

@test "awaiting_decision is not busy" {
  _busy '{"run":{"items":[{"key":"o/r#4","status":"awaiting_decision"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "a finished state is not busy" {
  _busy '{"run":{"items":[{"key":"o/r#5","status":"done"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "repo match is exact, not a prefix sibling" {
  _busy '{"run":{"items":[{"key":"o/r#1","status":"queued"},{"key":"o/r-extra#2","status":"queued"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[1]' ]
}

@test "same name under a different owner is not this repo" {
  _busy '{"run":{"items":[{"key":"other/r#1","status":"queued"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "identity splits on the last hash" {
  _busy '{"run":{"items":[{"key":"o/foo#bar#9","status":"queued"}]}}' 'o/foo#bar'
  [ "$status" -eq 0 ]
  [ "$output" = '[9]' ]
}

@test "results are numbers not strings" {
  _busy '{"run":{"items":[{"key":"o/r#12","status":"running"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  jq -e '.[0] == 12 and (.[0] | type) == "number"' <<<"$output"
}

@test "null status yields empty" {
  _busy 'null' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "no-run status yields empty" {
  _busy '{"active":false}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "empty items yields empty" {
  _busy '{"run":{"items":[]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "malformed items are skipped" {
  _busy '{"run":{"items":[null,"x",{"status":"queued"},{"key":1,"status":"queued"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[]' ]
}

@test "duplicate number is busy if any item is busy" {
  _busy '{"run":{"items":[{"key":"o/r#7","status":"done"},{"key":"o/r#7","status":"queued"}]}}' 'o/r'
  [ "$status" -eq 0 ]
  [ "$output" = '[7]' ]
}

@test "BUSY_STATES members are ItemStatus enum values" {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/batch.js');
    process.stdout.write(JSON.stringify(m.BUSY_STATES));
  "
  [ "$status" -eq 0 ]
  enum_vals=$(sed -n '/^class ItemStatus(StrEnum):/,/^class /p' "$REPO_ROOT/ai/lib/batch/model.py" | sed -n 's/^.*= "\([^"]*\)"$/\1/p')
  [ -n "$enum_vals" ]
  while IFS= read -r s || [[ -n "$s" ]]; do
    echo "$enum_vals" | grep -Fxq "$s"
  done < <(jq -r '.[]' <<<"$output")
}
