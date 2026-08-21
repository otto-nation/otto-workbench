#!/usr/bin/env bats
# Tests for bin/local/validate-doc-reference — the gate that keeps a module's
# `doc-group:` marker from being the only thing that ever mentions it.
#
# Every failing case runs against a copy of the real docs/ in TMPDIR, reached
# through DOCS_DIR: the source sets and their groups stay the live ones, so a
# fixture that passes here is the arrangement the repo actually ships.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATOR="$REPO_ROOT/bin/local/validate-doc-reference"
  FIXTURE="$TMPDIR/docs"
  mkdir -p "$FIXTURE"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _copy_docs — the real source docs, which render every group by construction.
_copy_docs() {
  cp "$REPO_ROOT"/docs/*.src.md "$FIXTURE/"
}

# _drop_directive DOC TEXT — removes the line holding TEXT from a fixture doc.
_drop_directive() {
  grep -v -- "$2" "$FIXTURE/$1" > "$FIXTURE/$1.tmp"
  mv "$FIXTURE/$1.tmp" "$FIXTURE/$1"
}

@test "the workbench docs render every group every set declares" {
  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "success names how many docs and groups were checked" {
  _copy_docs
  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 0 ]
  echo "$output" | grep -qE "[0-9]+ source docs render all [0-9]+ module groups"
}

@test "fails when no doc requests a declared group" {
  _copy_docs
  _drop_directive libraries.src.md "--set lib --group registry"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "no docs/\*.src.md renders the 'registry' group of the 'lib' set"
  echo "$output" | grep -q "Add: <!-- include: bin/local/generate-doc-reference --set lib --group registry -->"
}

@test "names the set a missing group belongs to" {
  _copy_docs
  _drop_directive ai-libraries.src.md "--set ai-lib --group backend"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "the 'backend' group of the 'ai-lib' set"
}

@test "counts every unrendered group rather than stopping at the first" {
  _copy_docs
  _drop_directive ai-libraries.src.md "--set ai-lib --group backend"
  _drop_directive ai-libraries.src.md "--set ai-lib --group eval"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "'backend' group"
  echo "$output" | grep -q "'eval' group"
}

@test "fails on a hand-written module section in a doc that renders them" {
  _copy_docs
  echo '### review_scout.py' >> "$FIXTURE/ai-libraries.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "ai-libraries.src.md: hand-written module section '### review_scout.py'"
  echo "$output" | grep -q "Module sections come from the module's own doc block"
}

@test "allows a level-three heading in a doc that renders no module sections" {
  _copy_docs
  # tools.src.md calls no generator, so its ### headings are ordinary prose —
  # the heading rule applies to the docs the generator writes sections into.
  echo '### Installing a tool' >> "$FIXTURE/tools.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "a doc requesting a group nobody declares is the generator's error" {
  _copy_docs
  # The reverse of the missing-directive rule: this validator passes it through,
  # and compose-docs surfaces it when the generator runs.
  echo '<!-- include: bin/local/generate-doc-reference --set lib --group nope -->' \
    >> "$FIXTURE/libraries.src.md"

  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "fails when the docs directory holds no sources" {
  DOCS_DIR="$FIXTURE" run "$VALIDATOR"
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "No .src.md files found"
}

@test "--quiet prints nothing on success" {
  _copy_docs
  DOCS_DIR="$FIXTURE" run "$VALIDATOR" --quiet
  [ "$status" -eq 0 ]
  [ -z "$output" ]
}

@test "--quiet still reports a failure" {
  _copy_docs
  _drop_directive libraries.src.md "--set lib --group registry"

  # validate-all invokes every validator with --quiet, so a failure the flag
  # swallowed would reach the summary as a bare name and no reason.
  DOCS_DIR="$FIXTURE" run "$VALIDATOR" --quiet
  [ "$status" -eq 1 ]
  echo "$output" | grep -q "'registry' group"
}

@test "--help prints usage and exits 0" {
  run "$VALIDATOR" --help
  [ "$status" -eq 0 ]
  echo "$output" | grep -q "Usage: validate-doc-reference"
}

@test "rejects an unknown flag with exit 2" {
  run "$VALIDATOR" --nope
  [ "$status" -eq 2 ]
  echo "$output" | grep -q "Unknown argument: --nope"
}
