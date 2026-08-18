#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  source_lib
  COMMITLINT_CONFIG=""
}

@test "conventions.sh defines BREAKING_CHANGE_FOOTER" {
  [[ "$BREAKING_CHANGE_FOOTER" == "BREAKING CHANGE" ]]
}

@test "conventions.sh defines NOT_BREAKING_FOOTER" {
  [[ "$NOT_BREAKING_FOOTER" == "Not-Breaking" ]]
}

@test "validate_commit_msg accepts a bang marker in the header" {
  run validate_commit_msg "feat!: drop the legacy flag"
  [ "$status" -eq 0 ]
}

@test "validate_commit_msg accepts a bang marker with a scope" {
  run validate_commit_msg "feat(pr)!: rename --post to --publish"
  [ "$status" -eq 0 ]
}

@test "validate_commit_msg still rejects a non-conventional header" {
  run validate_commit_msg "just some words"
  [ "$status" -eq 1 ]
}

@test "validate_commit_msg still rejects an unknown type with a bang" {
  run validate_commit_msg "wibble!: not a real type"
  [ "$status" -eq 1 ]
}
