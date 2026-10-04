#!/usr/bin/env bats
# Tests for validate-registries — schema, cross-file, reverse-bindir and work-registry validation.
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

@test "resolves REPO_ROOT from its own path, not an inherited GIT_DIR" {
  # setup() above sources the script with REPO_ROOT pre-set, so this
  # re-sources it fresh, in isolation, with REPO_ROOT unset and an inherited
  # GIT_DIR (e.g. from a git hook) pointing nowhere.
  local real_root
  real_root="$(cd "$BATS_TEST_DIRNAME/.." && pwd)"
  run env GIT_DIR="$TMPDIR/nowhere" GIT_WORK_TREE="$TMPDIR/nowhere" bash -c '
    source "$1"
    printf "%s\n" "$REPO_ROOT"
  ' _ "$BATS_TEST_DIRNAME/../bin/local/validate-registries"
  [ "$status" -eq 0 ]
  [ "$output" = "$real_root" ]
}
# ── Schema validation ─────────────────────────────────────────────────────────

@test "passes when all registries are valid" {
  _write_valid_brew
  _write_valid_bin
  _write_valid_zsh

  run main
  [ "$status" -eq 0 ]
}

@test "fails when brew entry is missing description" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  validation: brewfile
  source: brew/Brewfile

tools:
  - name: mytool
    permission: false
    visibility: full
    when_to_use: "When testing"
    usage: "mytool --help"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/Brewfile"

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: description"* ]]
}

@test "fails when brew entry is missing when_to_use" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  validation: brewfile
  source: brew/Brewfile

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A tool"
    usage: "mytool --help"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/Brewfile"

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: when_to_use"* ]]
}

@test "fails on duplicate tool names in brew registry" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  validation: brewfile
  source: brew/Brewfile

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "First"
    when_to_use: "Always"
    usage: "mytool --help"
  - name: mytool
    permission: false
    visibility: full
    description: "Second"
    when_to_use: "Always"
    usage: "mytool --help"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/Brewfile"

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"duplicate tool name: mytool"* ]]
}

# ── Cross-file validation ─────────────────────────────────────────────────────

@test "fails when brew registry entry not in Brewfile" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  validation: brewfile
  source: brew/Brewfile

tools:
  - name: missing-formula
    permission: false
    visibility: full
    description: "Not in Brewfile"
    when_to_use: "Never"
    usage: "missing-formula --help"
EOF
  printf 'brew "something-else"\n' > "$TMPDIR/brew/Brewfile"

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"not found in brew/Brewfile"* ]]
}

@test "passes when brew entry matches a cask in Brewfile" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  validation: brewfile
  source: brew/Brewfile

tools:
  - name: mycask
    permission: false
    visibility: full
    description: "A cask"
    when_to_use: "For GUI tools"
    usage: "mycask --help"
EOF
  printf 'cask "mycask"\n' > "$TMPDIR/brew/Brewfile"

  run main
  [ "$status" -eq 0 ]
}

@test "passes when brew_name override matches Brewfile entry" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  validation: brewfile
  source: brew/Brewfile

tools:
  - name: mvn
    permission: false
    visibility: full
    brew_name: maven
    description: "Maven build tool"
    when_to_use: "Building Maven projects"
    usage: "mvn --help"
EOF
  printf 'brew "maven"\n' > "$TMPDIR/brew/Brewfile"

  run main
  [ "$status" -eq 0 ]
}

@test "fails when bin registry entry has no matching file in bin/" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Workbench Scripts"
  validation: bindir
  source: bin

tools:
  - name: no-such-script
    permission: false
    visibility: full
    description: "Missing"
    when_to_use: "Never"
    usage: "no-such-script --help"
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"not found in bin/"* ]]
}

@test "fails when zsh registry entry has no matching comment in zsh/" {
  cat > "$TMPDIR/zsh/registry.yml" << 'EOF'
meta:
  section: "Shell Aliases"
  validation: zsh-comments
  source: zsh

tools:
  - name: "Nomatch aliases"
    permission: false
    visibility: full
    description: "Nothing matches"
    when_to_use: "Never"
    usage: "nomatch --help"
EOF
  # No matching comment in any zsh file

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"no matching comment found"* ]]
}

# ── Reverse bindir validation ─────────────────────────────────────────────────

@test "fails when bin script exists but is not in registry" {
  _write_valid_bin
  touch "$TMPDIR/bin/newtool" && chmod +x "$TMPDIR/bin/newtool"

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"not registered"* ]]
}

@test "passes reverse check when non-executable files are ignored" {
  _write_valid_bin
  touch "$TMPDIR/bin/datafile"

  run main
  [ "$status" -eq 0 ]
}

# ── Work registry validation ──────────────────────────────────────────────────

@test "validates work registry schema" {
  _write_valid_work

  run main
  [ "$status" -eq 0 ]
}

@test "fails when work registry entry not in its Brewfile" {
  cat > "$TMPDIR/brew/work/mystack.registry.yml" << 'EOF'
meta:
  section: "My Stack Tools"
  install_check: true
  validation: brewfile
  source: brew/work/mystack.Brewfile

tools:
  - name: missing-work-tool
    permission: false
    visibility: full
    description: "Not in Brewfile"
    when_to_use: "Never"
    usage: "missing-work-tool --help"
EOF
  printf 'brew "something-else"\n' > "$TMPDIR/brew/work/mystack.Brewfile"

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"not found in brew/work/mystack.Brewfile"* ]]
}

@test "passes work registry with brew_name override" {
  cat > "$TMPDIR/brew/work/mystack.registry.yml" << 'EOF'
meta:
  section: "My Stack Tools"
  install_check: true
  validation: brewfile
  source: brew/work/mystack.Brewfile

tools:
  - name: kubectl
    permission: false
    visibility: full
    brew_name: kubernetes-cli
    description: "Kubernetes CLI"
    when_to_use: "Managing clusters"
    usage: "kubectl --help"
EOF
  printf 'brew "kubernetes-cli"\n' > "$TMPDIR/brew/work/mystack.Brewfile"

  run main
  [ "$status" -eq 0 ]
}

# ── Missing registries ────────────────────────────────────────────────────────

@test "succeeds and warns when registries are missing" {
  # No registry files written

  run main
  [ "$status" -eq 0 ]
}
