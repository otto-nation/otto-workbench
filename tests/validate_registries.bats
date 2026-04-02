#!/usr/bin/env bats

setup() {
  load 'test_helper'
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"

  # Mirror minimum required repo structure in TMPDIR
  mkdir -p "$TMPDIR/bin" "$TMPDIR/brew/work" "$TMPDIR/zsh/config.d" "$TMPDIR/lib"
  cp "$REPO_ROOT/lib/ui.sh" "$TMPDIR/lib/ui.sh"
  cp "$REPO_ROOT/lib/constants.sh" "$TMPDIR/lib/constants.sh"
  cp "$REPO_ROOT/lib/registries.sh" "$TMPDIR/lib/registries.sh"

  # Stub bin scripts referenced in tests
  touch "$TMPDIR/bin/mytool" && chmod +x "$TMPDIR/bin/mytool"
  touch "$TMPDIR/bin/othertool" && chmod +x "$TMPDIR/bin/othertool"

  # Install validator pointing at TMPDIR via a wrapper that overrides REPO_ROOT
  VALIDATOR="$TMPDIR/validate-registries"
  sed "s|REPO_ROOT=.*|REPO_ROOT=\"$TMPDIR\"|" \
    "$REPO_ROOT/bin/validate-registries" > "$VALIDATOR"
  chmod +x "$VALIDATOR"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
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
    description: "A test tool"
    when_to_use: "When testing"
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
    description: "A script"
    when_to_use: "When needed"
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
    description: "Git shortcuts"
    when_to_use: "Always"
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
    description: "A work tool"
    when_to_use: "When working"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/work/mystack.Brewfile"
}

# ── Schema validation ─────────────────────────────────────────────────────────

@test "passes when all registries are valid" {
  _write_valid_brew
  _write_valid_bin
  _write_valid_zsh

  run bash "$VALIDATOR"
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
    when_to_use: "When testing"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/Brewfile"

  run bash "$VALIDATOR"
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
    description: "A tool"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/Brewfile"

  run bash "$VALIDATOR"
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
    description: "First"
    when_to_use: "Always"
  - name: mytool
    description: "Second"
    when_to_use: "Always"
EOF
  printf 'brew "mytool"\n' > "$TMPDIR/brew/Brewfile"

  run bash "$VALIDATOR"
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
    description: "Not in Brewfile"
    when_to_use: "Never"
EOF
  printf 'brew "something-else"\n' > "$TMPDIR/brew/Brewfile"

  run bash "$VALIDATOR"
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
    description: "A cask"
    when_to_use: "For GUI tools"
EOF
  printf 'cask "mycask"\n' > "$TMPDIR/brew/Brewfile"

  run bash "$VALIDATOR"
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
    brew_name: maven
    description: "Maven build tool"
    when_to_use: "Building Maven projects"
EOF
  printf 'brew "maven"\n' > "$TMPDIR/brew/Brewfile"

  run bash "$VALIDATOR"
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
    description: "Missing"
    when_to_use: "Never"
EOF

  run bash "$VALIDATOR"
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
    description: "Nothing matches"
    when_to_use: "Never"
EOF
  # No matching comment in any zsh file

  run bash "$VALIDATOR"
  [ "$status" -ne 0 ]
  [[ "$output" == *"no matching comment found"* ]]
}

# ── Work registry validation ──────────────────────────────────────────────────

@test "validates work registry schema" {
  _write_valid_work

  run bash "$VALIDATOR"
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
    description: "Not in Brewfile"
    when_to_use: "Never"
EOF
  printf 'brew "something-else"\n' > "$TMPDIR/brew/work/mystack.Brewfile"

  run bash "$VALIDATOR"
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
    brew_name: kubernetes-cli
    description: "Kubernetes CLI"
    when_to_use: "Managing clusters"
EOF
  printf 'brew "kubernetes-cli"\n' > "$TMPDIR/brew/work/mystack.Brewfile"

  run bash "$VALIDATOR"
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

  run bash "$VALIDATOR"
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

  run bash "$VALIDATOR"
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

  run bash "$VALIDATOR"
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

  run bash "$VALIDATOR"
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

  run bash "$VALIDATOR"
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

  run bash "$VALIDATOR"
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

  run bash "$VALIDATOR"
  [ "$status" -eq 0 ]
}

# ── Missing registries ────────────────────────────────────────────────────────

@test "succeeds and warns when registries are missing" {
  # No registry files written

  run bash "$VALIDATOR"
  [ "$status" -eq 0 ]
}
