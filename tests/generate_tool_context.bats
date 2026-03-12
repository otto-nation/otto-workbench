#!/usr/bin/env bats

setup() {
  load 'test_helper'
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"

  # Point all registries at nonexistent paths by default so the generator
  # never falls back to the real workbench registries during tests.
  export BREW_REGISTRY="$TMPDIR/brew.yml"
  export BIN_REGISTRY="$TMPDIR/bin.yml"
  export ZSH_REGISTRY="$TMPDIR/zsh.yml"
  export TOOL_CONTEXT_OUTPUT="$TMPDIR/tools.generated.md"

  GENERATOR="$REPO_ROOT/bin/generate-tool-context"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
  unset BREW_REGISTRY BIN_REGISTRY ZSH_REGISTRY TOOL_CONTEXT_OUTPUT
}

_write_registry() {
  local file="$1"
  cat > "$file" << 'EOF'
tools:
  - name: mytool
    description: "A test tool"
    when_to_use: "When testing"
    usage: "mytool --flag"
    docs: "https://example.com"
EOF
}

_write_minimal_registry() {
  local file="$1"
  cat > "$file" << 'EOF'
tools:
  - name: minimal
    description: "No optional fields"
    when_to_use: "Always"
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

@test "renders Brew Tools section from BREW_REGISTRY" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q "## Brew Tools" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders Workbench Scripts section from BIN_REGISTRY" {
  _write_registry "$BIN_REGISTRY"

  bash "$GENERATOR"
  grep -q "## Workbench Scripts" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders Shell Aliases section from ZSH_REGISTRY" {
  _write_registry "$ZSH_REGISTRY"

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

# ── Missing registries ────────────────────────────────────────────────────────

@test "succeeds when all registries are missing" {
  run bash "$GENERATOR"
  [ "$status" -eq 0 ]
}

@test "skips section for missing registry file" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q "## Brew Tools" "$TOOL_CONTEXT_OUTPUT"
  run grep "## Workbench Scripts" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

# ── Multiple entries ──────────────────────────────────────────────────────────

@test "renders multiple tool entries" {
  cat > "$BREW_REGISTRY" << 'EOF'
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
