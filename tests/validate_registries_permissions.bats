#!/usr/bin/env bats
# Tests for validate-registries — the permission field.
setup_file() {
  load 'test_helper'
  export BATS_NO_PARALLELIZE_WITHIN_FILE=true
  SHARED_DIR="$BATS_FILE_TMPDIR/validate"
  mkdir -p "$SHARED_DIR/bin" "$SHARED_DIR/brew/work" "$SHARED_DIR/zsh/config.d" "$SHARED_DIR/lib"

  # Copy every lib module rather than an enumerated subset — a new sub-module
  # sourced by lib/ui.sh would otherwise break this fixture on arrival.
  cp "$REPO_ROOT/lib/"*.sh "$SHARED_DIR/lib/"

  touch "$SHARED_DIR/bin/mytool" && chmod +x "$SHARED_DIR/bin/mytool"
  touch "$SHARED_DIR/bin/othertool" && chmod +x "$SHARED_DIR/bin/othertool"

  export SHARED_DIR
}

setup() {
  load 'test_helper'
  load 'validate_registries_helper'
  common_setup
  # Must source per-test: bats runs each test in a subshell, so functions
  # from setup_file() don't survive. The libs sourced by the script are
  # unavoidable overhead in bats's architecture.
  REPO_ROOT="$SHARED_DIR" source "$BATS_TEST_DIRNAME/../bin/local/validate-registries"
  REPO_ROOT="$SHARED_DIR"
  ORIG_DIR="$PWD"
  TMPDIR="$SHARED_DIR"

  # Clean mutable state from previous test
  rm -f "$TMPDIR/brew/registry.yml" "$TMPDIR/brew/Brewfile"
  rm -f "$TMPDIR/bin/registry.yml"
  rm -f "$TMPDIR/zsh/registry.yml"
  rm -f "$TMPDIR/zsh/config.d/"*
  rm -f "$TMPDIR/brew/work/"*.registry.yml "$TMPDIR/brew/work/"*.Brewfile
  for f in "$TMPDIR/bin/"*; do
    [[ -e "$f" ]] || continue
    case "${f##*/}" in mytool|othertool) ;; *) rm -f "$f" ;; esac
  done
}

teardown() {
  cd "$ORIG_DIR" || return 1
  common_teardown
}

# ── Permission field validation ───────────────────────────────────────────────

@test "passes with permission: true" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: true
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "passes with permission: false" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "passes with permission: string" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: "mt"
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "fails with permission: empty string" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: ""
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"permission string must be non-empty"* ]]
}

@test "passes with permission: array of Bash patterns" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission:
      - "Bash(mt sub:*)"
      - "Bash(mt other:*)"
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "fails with permission: integer (invalid type)" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: 42
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"permission must be boolean, string, or array"* ]]
}

@test "the reported permission type is the YAML tag, not a JSON approximation" {
  # The error names the tag it rejected, so !!float has to survive the read as
  # itself. A JSON round trip has one number type and would report !!int here.
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: 1.10
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"got !!float"* ]]
}

@test "an unquoted yes stays a string, as YAML 1.2 reads it" {
  # A YAML 1.1 reader calls this a bool and lets it through the !!bool arm.
  # yq is 1.2, so it is a string, and an empty-string permission is the thing
  # the string arm rejects. A reader swap that changed this would silently
  # turn a malformed permission into a valid one.
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: yes
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  # A non-empty string is a valid permission, so this passes — what matters is
  # that it took the !!str arm and was not read as the boolean true.
  [ "$status" -eq 0 ]
  [[ "$output" != *"permission must be boolean"* ]]
}

@test "a field present but empty still fails the required-field check" {
  # `description:` with no value is present and null. reg_has says it is there
  # and reg_get says it is empty; the check keys on the second. Collapsing the
  # two would make this registry pass.
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: full
    description:
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: description"* ]]
}

@test "fails when tools is a mapping rather than a list" {
  # The natural first way to write a registry. Every check iterates tools by
  # index, so a mapping yields no entries — without this gate the file is
  # reported clean with nothing examined.
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  mytool:
    permission: false
    visibility: full
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"tools must be a list of entries"* ]]
}

@test "a bad tools shape does not hide the other problems in the file" {
  # Only the checks that iterate entries are skipped. The meta-only ones do not
  # read tools, and skipping them would mean the shape has to be fixed and the
  # validator re-run before anything else in the file is even reported.
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none
  scope: "BAD SCOPE!"

tools:
  mytool:
    permission: false
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"tools must be a list of entries"* ]]
  [[ "$output" == *"meta.scope 'BAD SCOPE!'"* ]]
}

@test "a declared-empty tools is valid, not a crash" {
  # `tools:` with no value is a registry saying it has none. reg_len answers 0
  # for it as the `yq '.tools | length'` it replaced did — refusing it would
  # abort the run inside a bare `count=$(reg_len ...)` under errexit, with no
  # output at all rather than an error naming the file.
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "a declared-empty env is valid too" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools: []
env:
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "fails when env is a mapping rather than a list" {
  # The same shape error as tools, and for the same reason — the entry loop
  # would examine nothing. Reported against the file, not raised as an abort.
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools: []
env:
  MY_VAR:
    comment: "a var"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"env must be a list of entries"* ]]
}

@test "an explicit empty validation is reported, not defaulted to none" {
  # `// "none"` substituted on null, not on an empty string, so a registry that
  # writes one names a cross-check that does not exist and is told so. Only an
  # absent key means schema-only.
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: ""

tools: []
EOF

  run main
  # A warning, not an error — the cross-check is skipped, and the run still
  # passes. Pinned so it reads the same way as the absent-key sibling below.
  [ "$status" -eq 0 ]
  [[ "$output" == *"unknown validation type"* ]]
}

@test "an absent validation is schema-only and says nothing" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"

tools: []
EOF

  run main
  [ "$status" -eq 0 ]
  [[ "$output" != *"unknown validation type"* ]]
}

@test "fails on a registry that cannot be parsed" {
  # Reading each field separately answered an empty count for a malformed
  # file, so the entry loop ran zero times and every check on it passed. The
  # batch read fails by name instead.
  printf 'meta:\n  section: [\n' > "$TMPDIR/bin/registry.yml"

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"registry.yml"* ]]
}

@test "fails when permission is missing" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: permission"* ]]
}

@test "fails when visibility is missing" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: visibility"* ]]
}

@test "fails when visibility: brief entry has when_to_use" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A script"
    when_to_use: "When needed"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'when_to_use' is not allowed"* ]]
}

@test "fails when visibility: brief entry has usage" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A script"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'usage' is not allowed"* ]]
}

@test "fails when visibility: hidden entry has when_to_use" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: hidden
    description: "A script"
    when_to_use: "When needed"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'when_to_use' is not allowed"* ]]
}

@test "fails when visibility: hidden entry has usage" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: hidden
    description: "A script"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'usage' is not allowed"* ]]
}

@test "fails when visibility: full entry is missing usage" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A script"
    when_to_use: "When needed"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: usage"* ]]
}

@test "fails with unknown field" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
    bogus_field: "unexpected"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"unknown field 'bogus_field'"* ]]
}

@test "fails with permission array entry not matching Bash pattern" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission:
      - "not-a-bash-pattern"
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"must match Bash(...) pattern"* ]]
}
