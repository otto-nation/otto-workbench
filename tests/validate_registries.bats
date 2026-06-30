#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"

  # Mirror minimum required repo structure in TMPDIR
  mkdir -p "$TMPDIR/bin" "$TMPDIR/brew/work" "$TMPDIR/zsh/config.d" "$TMPDIR/lib"
  cp "$REPO_ROOT/lib/ui.sh" "$TMPDIR/lib/ui.sh"
  cp "$REPO_ROOT/lib/output.sh" "$TMPDIR/lib/output.sh"
  cp "$REPO_ROOT/lib/prompts.sh" "$TMPDIR/lib/prompts.sh"
  cp "$REPO_ROOT/lib/files.sh" "$TMPDIR/lib/files.sh"
  cp "$REPO_ROOT/lib/setup.sh" "$TMPDIR/lib/setup.sh"
  cp "$REPO_ROOT/lib/constants.sh" "$TMPDIR/lib/constants.sh"
  cp "$REPO_ROOT/lib/registries.sh" "$TMPDIR/lib/registries.sh"
  cp "$REPO_ROOT/lib/state.sh" "$TMPDIR/lib/state.sh"
  cp "$REPO_ROOT/lib/commands.sh" "$TMPDIR/lib/commands.sh"

  # Stub bin scripts referenced in tests
  touch "$TMPDIR/bin/mytool" && chmod +x "$TMPDIR/bin/mytool"
  touch "$TMPDIR/bin/othertool" && chmod +x "$TMPDIR/bin/othertool"

  # Install validator pointing at TMPDIR via a wrapper that overrides REPO_ROOT
  VALIDATOR="$TMPDIR/validate-registries"
  sed "s|REPO_ROOT=.*|REPO_ROOT=\"$TMPDIR\"|" \
    "$REPO_ROOT/bin/local/validate-registries" > "$VALIDATOR"
  chmod +x "$VALIDATOR"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
  common_teardown
}

_write_valid_brew() {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  install_check: false
  validation: brewfile
  source: brew/Brewfile

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A test tool"
    when_to_use: "When testing"
    usage: "mytool --help"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/Brewfile"
}

_write_valid_bin() {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Workbench Scripts"
  install_check: false
  validation: bindir
  source: bin

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
  - name: othertool
    permission: false
    visibility: full
    description: "Another script"
    when_to_use: "When needed"
    usage: "othertool --help"
EOF
}

_write_valid_zsh() {
  cat > "$TMPDIR/zsh/registry.yml" << 'EOF'
meta:
  section: "Shell Aliases"
  install_check: false
  validation: zsh-comments
  source: zsh

tools:
  - name: "Git aliases"
    permission: false
    visibility: full
    description: "Git shortcuts"
    when_to_use: "Always"
    usage: "gs"
EOF
  printf '# Git aliases Configuration\nalias gs="git status"\n' \
    > "$TMPDIR/zsh/config.d/aliases-git.zsh"
}

_write_valid_work() {
  cat > "$TMPDIR/brew/work/mystack.registry.yml" << 'EOF'
meta:
  section: "My Stack Tools"
  install_check: true
  validation: brewfile
  source: brew/work/mystack.Brewfile

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A work tool"
    when_to_use: "When working"
    usage: "mytool --help"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/work/mystack.Brewfile"
}

# ── Schema validation ─────────────────────────────────────────────────────────

@test "passes when all registries are valid" {
  _write_valid_brew
  _write_valid_bin
  _write_valid_zsh

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "fails when brew entry is missing description" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: description"* ]]
}

@test "fails when brew entry is missing when_to_use" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: when_to_use"* ]]
}

@test "fails on duplicate tool names in brew registry" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"duplicate tool name: mytool"* ]]
}

# ── Cross-file validation ─────────────────────────────────────────────────────

@test "fails when brew registry entry not in Brewfile" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not found in brew/Brewfile"* ]]
}

@test "passes when brew entry matches a cask in Brewfile" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "passes when brew_name override matches Brewfile entry" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "fails when bin registry entry has no matching file in bin/" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Workbench Scripts"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not found in bin/"* ]]
}

@test "fails when zsh registry entry has no matching comment in zsh/" {
  cat > "$TMPDIR/zsh/registry.yml" << 'EOF'
meta:
  section: "Shell Aliases"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no matching comment found"* ]]
}

# ── Reverse bindir validation ─────────────────────────────────────────────────

@test "fails when bin script exists but is not in registry" {
  _write_valid_bin
  touch "$TMPDIR/bin/newtool" && chmod +x "$TMPDIR/bin/newtool"

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"not registered"* ]]
}

@test "passes reverse check when non-executable files are ignored" {
  _write_valid_bin
  touch "$TMPDIR/bin/datafile"

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

# ── Work registry validation ──────────────────────────────────────────────────

@test "validates work registry schema" {
  _write_valid_work

  run "$VALIDATOR"
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

  run "$VALIDATOR"
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

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

# ── Env field validation ─────────────────────────────────────────────────────

@test "passes with valid env entries" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

env:
  - var: MY_CONFIG_VAR
    comment: "A config var"
    default: "default"

tools: []
EOF

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "fails when env entry is missing var" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

env:
  - comment: "No var field"

tools: []
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: var"* ]]
}

@test "fails when env var name is invalid" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

env:
  - var: lower_case_bad

tools: []
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"invalid var name"* ]]
}

@test "fails on duplicate env var within same registry" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

env:
  - var: MY_VAR
  - var: MY_VAR

tools: []
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"duplicate env var: MY_VAR"* ]]
}

@test "fails on duplicate env var across registries" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Brew Tools"
  install_check: false
  validation: none

env:
  - var: SHARED_VAR

tools: []
EOF
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Bin Tools"
  install_check: false
  validation: none

env:
  - var: SHARED_VAR

tools: []
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"defined in multiple registries"* ]]
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

  run "$VALIDATOR"
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

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

# ── Permission field validation ───────────────────────────────────────────────

@test "passes with permission: true" {
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
    usage: "mytool --help"
EOF

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "passes with permission: false" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "passes with permission: string" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: "mt"
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "fails with permission: empty string" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: ""
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"permission string must be non-empty"* ]]
}

@test "passes with permission: array of Bash patterns" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "fails with permission: integer (invalid type)" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: 42
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"permission must be boolean, string, or array"* ]]
}

@test "fails when permission is missing" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    visibility: full
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: permission"* ]]
}

@test "fails when visibility is missing" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    description: "A script"
    when_to_use: "When needed"
    usage: "mytool --help"
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: visibility"* ]]
}

@test "fails when visibility: brief entry has when_to_use" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A script"
    when_to_use: "When needed"
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'when_to_use' is not allowed"* ]]
}

@test "fails when visibility: brief entry has usage" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A script"
    usage: "mytool --help"
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'usage' is not allowed"* ]]
}

@test "fails when visibility: hidden entry has when_to_use" {
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
    when_to_use: "When needed"
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'when_to_use' is not allowed"* ]]
}

@test "fails when visibility: hidden entry has usage" {
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
    usage: "mytool --help"
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"field 'usage' is not allowed"* ]]
}

@test "fails when visibility: full entry is missing usage" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A script"
    when_to_use: "When needed"
EOF

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"missing required field: usage"* ]]
}

@test "fails with unknown field" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"unknown field 'bogus_field'"* ]]
}

@test "fails with permission array entry not matching Bash pattern" {
  cat > "$TMPDIR/bin/registry.yml" << 'EOF'
meta:
  section: "Test"
  install_check: false
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

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"must match Bash(...) pattern"* ]]
}

# ── meta.scope ────────────────────────────────────────────────────────────────

@test "passes with valid meta.scope" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  scope: go
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A tool"
EOF
  echo "brew \"mytool\"" > "$TMPDIR/brew/Brewfile"

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

@test "fails with invalid meta.scope characters" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  scope: "Go Tools"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A tool"
EOF
  echo "brew \"mytool\"" > "$TMPDIR/brew/Brewfile"

  run "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"must be lowercase alphanumeric"* ]]
}

@test "passes with hyphenated meta.scope" {
  cat > "$TMPDIR/brew/registry.yml" << 'EOF'
meta:
  section: "Tools"
  scope: "cloud-infra"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: brief
    description: "A tool"
EOF
  echo "brew \"mytool\"" > "$TMPDIR/brew/Brewfile"

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}

# ── Missing registries ────────────────────────────────────────────────────────

@test "succeeds and warns when registries are missing" {
  # No registry files written

  run "$VALIDATOR"
  [ "$status" -eq 0 ]
}
