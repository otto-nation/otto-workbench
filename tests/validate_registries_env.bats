#!/usr/bin/env bats
# Tests for validate-registries — the env field.
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

# ── Env field validation ─────────────────────────────────────────────────────

@test "passes with valid env entries" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none

env:
  - var: MY_CONFIG_VAR
    comment: "A config var"
    default: "default"

tools: []
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "fails when env entry is missing var" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none

env:
  - comment: "No var field"

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: var"* ]]
}

@test "fails when env var name is invalid" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none

env:
  - var: lower_case_bad

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"invalid var name"* ]]
}

@test "fails on duplicate env var within same registry" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none

env:
  - var: MY_VAR
  - var: MY_VAR

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"duplicate env var: MY_VAR"* ]]
}

@test "fails on an unknown field in an env entry" {
  # The gap this closes: env[] had no unknown-field check, so a misspelled
  # `target` was accepted and the variable never appeared under the name its
  # consumer read.
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none
  claude_env: true

env:
  - var: MY_VAR
    targett: OTHER_NAME

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"env 'MY_VAR': unknown field 'targett'"* ]]
}

@test "accepts every field KNOWN_ENV_FIELDS names" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none
  claude_env: true

env:
  - var: MY_VAR
    target: OTHER_NAME
    comment: "a comment"
    default: "a default"
    setup_url: https://example.com
    prefix: "pre_"
    claude_env: false
    role: model-tier

tools: []
EOF

  run main
  [ "$status" -eq 0 ]
}

@test "fails on a non-boolean claude_env in an env entry" {
  # collect_claude_env_vars excludes on an exact `false` and includes on
  # anything else, so an unrejected typo publishes a variable to a
  # world-readable settings.json rather than withholding one.
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none
  claude_env: true

env:
  - var: MY_VAR
    claude_env: flase

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"claude_env must be true or false, got 'flase'"* ]]
}

@test "fails on claude_env in an entry when the registry is not flagged" {
  # The field narrows a registry that has opted in; it cannot opt one in. Here
  # it decides nothing while reading as though it does.
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none

env:
  - var: MY_VAR
    claude_env: false

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"requires meta.claude_env: true"* ]]
}

@test "fails on an unknown role value" {
  # Checked strictly, unlike claude_env's lenient read: a role that excluded
  # would silently drop a tier from every harness, surfacing only as a model
  # quietly missing from a menu.
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none

env:
  - var: MY_VAR
    role: model-defualt

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"role must be one of"* ]]
}

@test "fails when two registries both claim model-default" {
  # The winner would otherwise depend on registry iteration order, so the
  # machine gets a default nobody chose and each file looks correct alone.
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  validation: none

env:
  - var: FIRST_MODEL
    role: model-default

tools: []
EOF
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Bin"
  validation: none

env:
  - var: SECOND_MODEL
    role: model-default

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"more than one env var declares role: model-default"* ]]
}

@test "fails on duplicate env var across registries" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  validation: none

env:
  - var: SHARED_VAR

tools: []
EOF
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Bin Tools"
  validation: none

env:
  - var: SHARED_VAR

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"defined in multiple registries"* ]]
}

@test "three registries claiming one env var are named in one error" {
  # Pairwise chaining reported "a and b" then "b and c": two errors for one
  # name, neither naming all three files.
  for d in brew bin zsh; do
    cat > "$TMPDIR/$d/registry.yml" << EOF
meta:
  section: "$d"
  validation: none

env:
  - var: SHARED_VAR

tools: []
EOF
  done

  run main
  [ "$status" -ne 0 ]
  [ "$(grep -c "defined in multiple registries" <<< "$output")" -eq 1 ]
  # Pins all three sources on the one line, in collect_registries' path
  # order (bin, brew, zsh) — not just present somewhere in the output, which
  # a regression splitting the accumulated sources across two lines would
  # still satisfy.
  [[ "$output" == *"env var 'SHARED_VAR' defined in multiple registries: bin/registry.yml brew/registry.yml zsh/registry.yml"* ]]
}

@test "fails on one tool name registered in two bindir registries" {
  # The MCP server cannot refuse this: it discovers in the thread that also
  # serves re-discovery, so it keeps the first entry and logs the rest —
  # which leaves registry order deciding which tool a client reaches.
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "A"
  validation: bindir
  source: bin

tools:
  - name: mytool
    permission: false
    visibility: hidden
    description: "one"
  - name: othertool
    permission: false
    visibility: hidden
    description: "and the other file, so the reverse check passes"
EOF
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "B"
  validation: bindir
  source: bin

tools:
  - name: mytool
    permission: false
    visibility: hidden
    description: "the same name, from a second registry"
  - name: othertool
    permission: false
    visibility: hidden
    description: "and the other file, so the reverse check passes"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"registered in multiple registries"* ]]
}

@test "three registries claiming one tool name name the kept file first" {
  # Which file wins is the actionable half: discovery keeps the first in
  # registry order, so the error lists it ahead of the ones it shadows.
  for d in bin brew zsh; do
    cat > "$TMPDIR/$d/registry.yml" << EOF
meta:
  section: "$d"
  validation: bindir
  source: bin

tools:
  - name: mytool
    permission: false
    visibility: hidden
    description: "claimed by $d"
  - name: othertool
    permission: false
    visibility: hidden
    description: "so the reverse check passes"
EOF
  done

  run main
  [ "$status" -ne 0 ]
  # One line per over-claimed name, not one per colliding pair. Both names in
  # this fixture are shared by all three files, so two lines is the whole of it
  # — pairwise chaining would have emitted four.
  [ "$(grep -c "registered in multiple registries" <<< "$output")" -eq 2 ]
  # collect_registries walks in path order, so bin/ is the claimant kept.
  [[ "$output" == *"tool 'mytool' registered in multiple registries: bin/registry.yml brew/registry.yml zsh/registry.yml"* ]]
}

# passes-at-base: the base has no cross-file check at all, so its scope is
# trivially satisfied there. Deleting the `bindir` guard from the check does
# fail this, which is what it is here to hold.
@test "a name shared by a brew stack and an env alias is not a collision" {
  # `linear` is a brew formula and an auth alias on the live tree. Neither is
  # a script the server can offer, so comparing across those namespaces would
  # report a collision that cannot happen.
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "A"
  validation: none

tools:
  - name: shared
    permission: false
    visibility: hidden
    description: "a brew formula"
EOF
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "B"
  validation: none

tools:
  - name: shared
    permission: false
    visibility: hidden
    description: "something else entirely"
EOF

  run main
  # Exit 0, not merely the absence of the message: an absence is also what a
  # run that died on an unrelated schema error produces, and a fixture missing
  # a required field reads as this test passing when nothing reached the scope
  # check at all.
  [ "$status" -eq 0 ]
  [[ "$output" != *"registered in multiple registries"* ]]
}

@test "fails when install_check true with empty tools and no install_check_command" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  install_check: true
  validation: none

env:
  - var: MY_VAR

tools: []
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"requires install_check_command"* ]]
}

@test "passes when install_check true with empty tools and install_check_command set" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  install_check: true
  install_check_command: sh
  validation: none

env:
  - var: MY_VAR

tools: []
EOF

  run main
  [ "$status" -eq 0 ]
}
