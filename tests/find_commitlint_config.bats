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

@test "finds commitlint.config.{ext}" {
  for ext in js cjs mjs ts cts mts; do
    local filename="commitlint.config.$ext"
    touch $filename
    find_commitlint_config
    [ "$COMMITLINT_CONFIG" = $filename ]
  
    #cleanup
    rm $filename 
  done
}

@test "finds .github/.commitlintrc.{ext}" {
  mkdir -p .github
  for ext in json yaml yml js cjs mjs ts cts mts; do
    local filename=".github/.commitlintrc.$ext"
    touch $filename 
    find_commitlint_config
    [ "$COMMITLINT_CONFIG" = "$filename" ]

    #cleanup
    rm $filename 
  done
}

@test "finds .github/.commitlintrc" {
  mkdir -p .github
  local filename=".github/.commitlintrc"
  touch $filename 
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = "$filename" ]
}

@test "finds .commitlintrc.json" {
  for ext in json yaml yml js cjs mjs ts cts mts; do
    local filename=".commitlintrc.$ext"
    touch $filename 
    find_commitlint_config
    [ "$COMMITLINT_CONFIG" = "$filename" ]

    #cleanup
    rm $filename 
  done
}

@test "finds .commitlintrc" {
  local filename=".commitlintrc"
  touch $filename 
  find_commitlint_config
  [ "$COMMITLINT_CONFIG" = "$filename" ]
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
