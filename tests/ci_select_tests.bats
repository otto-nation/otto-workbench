#!/usr/bin/env bats
# The CI workflow's contract for change-based test selection: flags only,
# required check names unchanged, no path filters, full history on test jobs.
#
# select-on: .github/workflows/ci.yml

setup() {
  load 'test_helper'
  common_setup
  CI_YML="$REPO_ROOT/.github/workflows/ci.yml"
}

@test "required check names are unchanged" {
  run yq -r '.jobs."bats-shard".name' "$CI_YML"
  [ "$status" -eq 0 ]
  [ "$output" = 'bats shard ${{ matrix.shard }}' ]
  run yq -r '.jobs."test-bats".name' "$CI_YML"
  [ "$output" = 'Tests (bats)' ]
  run yq -r '.jobs."test-pytest".name' "$CI_YML"
  [ "$output" = 'Tests (pytest)' ]
}

@test "bats shards pass --changed only on pull_request" {
  run yq -r '.jobs."bats-shard".steps[] | select(.name == "Run bats tests (shard ${{ matrix.shard }}/3)") | .run' "$CI_YML"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--changed"* ]]
  [[ "$output" == *"--base"* ]]
  [[ "$output" == *"github.event_name"* ]]
  [[ "$output" == *"pull_request"* ]]
  [[ "$output" == *"github.base_ref"* ]]
  [[ "$output" == *"--shard"* ]]
}

@test "pytest passes --changed only on pull_request" {
  run yq -r '.jobs."test-pytest".steps[] | select(.name == "Run pytest") | .run' "$CI_YML"
  [ "$status" -eq 0 ]
  [[ "$output" == *"--changed"* ]]
  [[ "$output" == *"--base"* ]]
  [[ "$output" == *"github.event_name"* ]]
  [[ "$output" == *"pull_request"* ]]
  [[ "$output" == *"github.base_ref"* ]]
}

@test "the workflow has no paths filters" {
  run yq -r '.on.pull_request.paths' "$CI_YML"
  [ "$output" = "null" ]
  run yq -r '.on.push.paths' "$CI_YML"
  [ "$output" = "null" ]
}

@test "test jobs fetch full history" {
  run yq -r '.jobs."bats-shard".steps[] | select(.uses // "" | test("actions/checkout@")) | .with."fetch-depth"' "$CI_YML"
  [ "$status" -eq 0 ]
  [ "$output" = "0" ]
  run yq -r '.jobs."test-pytest".steps[] | select(.uses // "" | test("actions/checkout@")) | .with."fetch-depth"' "$CI_YML"
  [ "$output" = "0" ]
}

@test "only the test jobs pass --changed" {
  run yq -r '.jobs | to_entries[] | select(.value.steps[]? | .run // "" | test("--changed")) | .key' "$CI_YML"
  [ "$status" -eq 0 ]
  [ "$output" = $'bats-shard\ntest-pytest' ]
}

@test "bats timings are uploaded from main only, never from a PR's partial run" {
  run yq -r '.jobs."bats-shard".steps[] | select(.name == "Upload timings") | .if' "$CI_YML"
  [ "$status" -eq 0 ]
  [[ "$output" == *"github.event_name != 'pull_request'"* ]]
}
