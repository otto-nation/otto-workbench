#!/usr/bin/env bats
# Tests for validate-registries — meta.scope and the commands, reference and parser fields.
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

# ── meta.scope ────────────────────────────────────────────────────────────────

@test "passes with valid meta.scope" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  scope: go
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A tool"
EOF
  echo "brew \"mytool\"" > "$TMPDIR/brew/Brewfile"

  run main
  [ "$status" -eq 0 ]
}

@test "fails with invalid meta.scope characters" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  scope: "Go Tools"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A tool"
EOF
  echo "brew \"mytool\"" > "$TMPDIR/brew/Brewfile"

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"must be lowercase alphanumeric"* ]]
}

@test "passes with hyphenated meta.scope" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  scope: "cloud-infra"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A tool"
EOF
  echo "brew \"mytool\"" > "$TMPDIR/brew/Brewfile"

  run main
  [ "$status" -eq 0 ]
}

# ── Commands field validation ─────────────────────────────────────────────────

@test "passes with valid commands field" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: true
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool sub1 | mytool sub2"
    commands:
      - name: sub1
        description: "First subcommand"
      - name: sub2
        description: "Second subcommand"
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "passes with optional fields in commands entry" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: true
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool sub1"
    commands:
      - name: sub1
        description: "First subcommand"
        scope: "All"
        when: "Always"
        detail: "Detailed explanation"
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "fails when commands entry is missing name" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: true
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool sub1"
    commands:
      - description: "No name field"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: name"* ]]
}

@test "fails when commands entry is missing description" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: true
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool sub1"
    commands:
      - name: sub1
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: description"* ]]
}

@test "fails when commands entry has unknown field" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: true
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool sub1"
    commands:
      - name: sub1
        description: "First subcommand"
        bogus: "unexpected"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"unknown field 'bogus'"* ]]
}

@test "fails when commands has duplicate command names" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: true
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool sub1"
    commands:
      - name: sub1
        description: "First"
      - name: sub1
        description: "Duplicate"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"duplicate command name: sub1"* ]]
}

@test "fails when commands on visibility: brief entry" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: true
    visibility: brief
    description: "A script"
    commands:
      - name: sub1
        description: "First"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'commands' is not allowed"* ]]
}

@test "fails when commands on visibility: hidden entry" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: hidden
    description: "A script"
    commands:
      - name: sub1
        description: "First"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'commands' is not allowed"* ]]
}

# ── Reference field validation ────────────────────────────────────────────────

# _bin_with_reference VALUE [META_EXTRA] — a bindir registry whose one tool sets
# `reference: VALUE`, beside the othertool the shared fixture also carries.
_bin_with_reference() {
  cat > "$TMPDIR/bin/registry.yml" << EOF
meta:
  section: "Workbench Scripts"
  validation: bindir
  source: bin
${2:-}
tools:
  - name: mytool
    permission: false
    visibility: hidden
    description: "A script"
    reference: $1
  - name: othertool
    permission: false
    visibility: hidden
    description: "Another script"
EOF
}

@test "passes with reference: true on a hidden bindir tool" {
  _bin_with_reference true
  run main
  [ "$status" -eq 0 ]
}

@test "fails with a non-boolean reference" {
  _bin_with_reference yes-please
  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"reference must be true or false (got !!str)"* ]]
}

@test "fails with reference in a workbench-scoped registry, where nothing reads it" {
  _bin_with_reference false "  scope: workbench"
  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'reference' is read only in a bindir registry outside the workbench scope"* ]]
}

@test "fails with reference in a registry that is not bindir" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: hidden
    description: "A script"
    reference: true
EOF
  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'reference' is read only"* ]]
}

# ── Parser field validation ───────────────────────────────────────────────────

# _bin_with_parser VISIBILITY EXTRA [SPEC] — a bindir registry whose mytool
# declares a parser (SPEC, default cli.mytool:build_parser), plus EXTRA lines
# (already indented) on the same entry.
_bin_with_parser() {
  cat > "$TMPDIR/bin/registry.yml" << EOF
meta:
  section: "Workbench Scripts"
  validation: bindir
  source: bin

tools:
  - name: mytool
    permission: false
    visibility: $1
    description: "A script"
    parser: ${3:-cli.mytool:build_parser}
$2
  - name: othertool
    permission: false
    visibility: hidden
    description: "Another script"
EOF
}

@test "a full tool with a parser needs no usage" {
  _bin_with_parser full '    when_to_use: "When needed"'
  run main
  [ "$status" -eq 0 ]
}

@test "a usage beside a parser fails — it would be a second copy" {
  _bin_with_parser full '    when_to_use: "When needed"
    usage: "mytool --help"'
  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'usage' is not allowed beside 'parser'"* ]]
}

@test "a full tool with neither usage nor parser fails" {
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
  [[ "$output" == *"missing required field: usage or parser"* ]]
}

@test "a malformed parser spec fails" {
  _bin_with_parser brief '' cli.mytool
  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"parser must be <module-or-path>:<attr>"* ]]
}

@test "a parser path that names no file fails" {
  _bin_with_parser brief '' bin/mytol:build_parser
  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"parser path 'bin/mytol' does not exist"* ]]
}

@test "a parser path that names a file passes" {
  _bin_with_parser brief '' bin/mytool:build_parser
  run main
  [ "$status" -eq 0 ]
}

@test "a usage beside a parser on a non-full tool is reported once" {
  _bin_with_parser brief '    usage: "mytool"'
  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'usage' is not allowed beside 'parser'"* ]]
  [[ "$output" != *"field 'usage' is not allowed when visibility"* ]]
}
