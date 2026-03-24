#!/usr/bin/env bats

setup() {
  load 'test_helper'
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"

  # Point all generator inputs/outputs at temp paths so tests never touch
  # real workbench files (registry data, tools.generated.md, README.md).
  export BREW_REGISTRY="$TMPDIR/brew.yml"
  export BIN_REGISTRY="$TMPDIR/bin.yml"
  export ZSH_REGISTRY="$TMPDIR/zsh.yml"
  export BREW_STACKS_DIR="$TMPDIR"
  export WORK_DIR="$TMPDIR/work"
  export TOOL_CONTEXT_OUTPUT="$TMPDIR/tools.generated.md"
  export README_PATH="$TMPDIR/README.md"
  export TASKFILE_PATH="$TMPDIR/Taskfile.yml"
  export AI_DIR="$TMPDIR/ai"

  mkdir -p "$WORK_DIR"
  GENERATOR="$REPO_ROOT/bin/generate-tool-context"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
  unset BREW_REGISTRY BIN_REGISTRY ZSH_REGISTRY BREW_STACKS_DIR WORK_DIR TOOL_CONTEXT_OUTPUT
}

# _write_registry FILE SECTION — writes a single-tool registry with the given section title
_write_registry() {
  local file="$1" section="${2:-Tools}"
  cat > "$file" << EOF
meta:
  section: "$section"
  install_check: false
  validation: none

tools:
  - name: mytool
    description: "A test tool"
    when_to_use: "When testing"
    usage: "mytool --flag"
    docs: "https://example.com"
EOF
}

# _write_minimal_registry FILE — writes a registry with no optional fields
_write_minimal_registry() {
  local file="$1"
  cat > "$file" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

tools:
  - name: minimal
    description: "No optional fields"
    when_to_use: "Always"
EOF
}

# _write_install_checked_registry FILE TOOL_NAME — writes a registry with install_check: true
_write_install_checked_registry() {
  local file="$1" tool_name="$2"
  cat > "$file" << EOF
meta:
  section: "Work Tools"
  install_check: true
  validation: none

tools:
  - name: $tool_name
    description: "An install-checked tool"
    when_to_use: "When installed"
EOF
}

# ── Output file ───────────────────────────────────────────────────────────────

@test "creates the output file" {
  _write_registry "$BREW_REGISTRY"

  run bash "$GENERATOR"
  [ "$status" -eq 0 ]
  [ -f "$TOOL_CONTEXT_OUTPUT" ]
}

@test "output contains auto-generated header comment" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q "AUTO-GENERATED" "$TOOL_CONTEXT_OUTPUT"
}

# ── Section rendering ─────────────────────────────────────────────────────────

@test "renders section title from meta.section in BREW_REGISTRY" {
  _write_registry "$BREW_REGISTRY" "Brew Tools"

  bash "$GENERATOR"
  grep -q "## Brew Tools" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders section title from meta.section in BIN_REGISTRY" {
  _write_registry "$BIN_REGISTRY" "Workbench Scripts"

  bash "$GENERATOR"
  grep -q "## Workbench Scripts" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders section title from meta.section in ZSH_REGISTRY" {
  _write_registry "$ZSH_REGISTRY" "Shell Aliases"

  bash "$GENERATOR"
  grep -q "## Shell Aliases" "$TOOL_CONTEXT_OUTPUT"
}

# ── Tool entry fields ─────────────────────────────────────────────────────────

@test "renders tool name as H3" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q "### mytool" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders tool description" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q "A test tool" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders when_to_use field" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q "When testing" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders usage field when present" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q "mytool --flag" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders docs field when present" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q "https://example.com" "$TOOL_CONTEXT_OUTPUT"
}

@test "omits usage line when usage is absent" {
  _write_minimal_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  run grep "Usage" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "omits docs line when docs is absent" {
  _write_minimal_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  run grep "Docs" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

# ── install_check filtering ───────────────────────────────────────────────────

@test "includes install-checked tool when it is in PATH" {
  # sh is always available; use it as the tool name so command -v succeeds
  _write_install_checked_registry "$WORK_DIR/test.registry.yml" "sh"

  bash "$GENERATOR"
  grep -q "### sh" "$TOOL_CONTEXT_OUTPUT"
}

@test "excludes install-checked tool when it is not in PATH" {
  _write_install_checked_registry "$WORK_DIR/test.registry.yml" "definitely-not-a-real-tool-xyzzy"

  bash "$GENERATOR"
  run grep "### definitely-not-a-real-tool-xyzzy" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "omits section header when all install-checked tools are absent" {
  _write_install_checked_registry "$WORK_DIR/test.registry.yml" "definitely-not-a-real-tool-xyzzy"

  bash "$GENERATOR"
  run grep "## Work Tools" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "renders work registry section when tool is installed" {
  _write_install_checked_registry "$WORK_DIR/test.registry.yml" "sh"

  bash "$GENERATOR"
  grep -q "## Work Tools" "$TOOL_CONTEXT_OUTPUT"
}

# ── Missing registries ────────────────────────────────────────────────────────

@test "succeeds when all registries are missing" {
  run bash "$GENERATOR"
  [ "$status" -eq 0 ]
}

@test "skips section for missing registry file" {
  _write_registry "$BREW_REGISTRY" "Brew Tools"

  bash "$GENERATOR"
  grep -q "## Brew Tools" "$TOOL_CONTEXT_OUTPUT"
  run grep "## Workbench Scripts" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

# ── Multiple entries ──────────────────────────────────────────────────────────

@test "renders multiple tool entries" {
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

tools:
  - name: tool-a
    description: "First tool"
    when_to_use: "First"
  - name: tool-b
    description: "Second tool"
    when_to_use: "Second"
EOF

  bash "$GENERATOR"
  grep -q "### tool-a" "$TOOL_CONTEXT_OUTPUT"
  grep -q "### tool-b" "$TOOL_CONTEXT_OUTPUT"
}
