#!/usr/bin/env bats

setup() {
  load 'test_helper'
  source_lib
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"
  cd "$TMPDIR"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
}

@test "returns empty when no config exists" {
  find_commitlint_config
  [ -z "$COMMITLINT_CONFIG" ]
}

@test "finds commitlint.config.js" {
  touch commitlint.config.js
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = "commitlint.config.js" ]
}

@test "finds commitlint.config.mjs" {
  touch commitlint.config.mjs
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = "commitlint.config.mjs" ]
}

@test "finds commitlint.config.cjs" {
  touch commitlint.config.cjs
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = "commitlint.config.cjs" ]
}

@test "finds .github/.commitlintrc.mjs" {
  mkdir -p .github
  touch .github/.commitlintrc.mjs
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = ".github/.commitlintrc.mjs" ]
}

@test "finds .github/.commitlintrc.json" {
  mkdir -p .github
  touch .github/.commitlintrc.json
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = ".github/.commitlintrc.json" ]
}

@test "finds .commitlintrc.json" {
  touch .commitlintrc.json
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = ".commitlintrc.json" ]
}

@test "finds .commitlintrc.js" {
  touch .commitlintrc.js
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = ".commitlintrc.js" ]
}

@test "commitlint.config.js takes priority over .commitlintrc.json" {
  touch commitlint.config.js
  touch .commitlintrc.json
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = "commitlint.config.js" ]
}

@test ".github/.commitlintrc.mjs takes priority over .commitlintrc.json" {
  mkdir -p .github
  touch .github/.commitlintrc.mjs
  touch .commitlintrc.json
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = ".github/.commitlintrc.mjs" ]
}
