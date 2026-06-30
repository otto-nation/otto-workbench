#!/usr/bin/env bats

setup() {
  load 'test_helper'
  common_setup
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"

  # Point all generator inputs/outputs at temp paths so tests never touch
  # real workbench files (registry data, tools.generated.md, README.md).
  mkdir -p "$TMPDIR/brew" "$TMPDIR/bin" "$TMPDIR/zsh" "$TMPDIR/mise" "$TMPDIR/docs"
  export BREW_REGISTRY="$TMPDIR/brew/registry.yml"
  export MISE_REGISTRY="$TMPDIR/mise/registry.yml"
  export BIN_REGISTRY="$TMPDIR/bin/registry.yml"
  export ZSH_REGISTRY="$TMPDIR/zsh/registry.yml"
  export BREW_STACKS_DIR="$TMPDIR"
  export WORK_DIR="$TMPDIR/work"
  export TOOL_CONTEXT_OUTPUT="$TMPDIR/tools.generated.md"
  export README_PATH="$TMPDIR/README.md"
  export TASKFILE_PATH="$TMPDIR/Taskfile.yml"
  export AI_DIR="$TMPDIR/ai"
  export REGISTRY_SCAN_DIR="$TMPDIR"
  export DOCS_DIR="$TMPDIR/docs"
  export TOOLS_DOC_PATH="$TMPDIR/docs/tools.md"
  export AI_DOC_PATH="$TMPDIR/docs/ai-automation.md"
  export COMPONENTS_DOC_PATH="$TMPDIR/docs/components.md"

  mkdir -p "$WORK_DIR"
  GENERATOR="$REPO_ROOT/bin/local/generate-tool-context"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
  common_teardown
  unset BREW_REGISTRY MISE_REGISTRY BIN_REGISTRY ZSH_REGISTRY BREW_STACKS_DIR WORK_DIR TOOL_CONTEXT_OUTPUT REGISTRY_SCAN_DIR AI_DIR README_PATH TASKFILE_PATH DOCS_DIR TOOLS_DOC_PATH AI_DOC_PATH COMPONENTS_DOC_PATH
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
    permission: false
    visibility: full
    description: "A test tool"
    when_to_use: "When testing"
    usage: "mytool --flag"
    docs: "https://example.com"
EOF
}

# _write_minimal_registry FILE — writes a registry with minimal required fields
_write_minimal_registry() {
  local file="$1"
  cat > "$file" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

tools:
  - name: minimal
    permission: false
    visibility: full
    description: "No optional fields"
    when_to_use: "Always"
    usage: "minimal --help"
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
    permission: false
    visibility: full
    description: "An install-checked tool"
    when_to_use: "When installed"
    usage: "$tool_name --help"
EOF
}

# _write_visibility_registry FILE SECTION — writes a registry with tools at different visibility tiers
_write_visibility_registry() {
  local file="$1" section="${2:-Visibility Tools}"
  cat > "$file" << EOF
meta:
  section: "$section"
  install_check: false
  validation: none

tools:
  - name: full-tool
    permission: false
    visibility: full
    description: "A full visibility tool"
    when_to_use: "Always available"
    usage: "full-tool --run"
  - name: ref-tool
    permission: false
    visibility: brief
    description: "A brief-only tool"
  - name: hidden-tool
    permission: false
    visibility: hidden
    description: "A hidden tool"
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

@test "omits docs field from output" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  run grep "https://example.com" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "omits usage line for visibility: brief entry" {
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

tools:
  - name: brief-tool
    permission: false
    visibility: brief
    description: "A brief tool"
EOF

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
    permission: false
    visibility: full
    description: "First tool"
    when_to_use: "First"
    usage: "tool-a --help"
  - name: tool-b
    permission: false
    visibility: full
    description: "Second tool"
    when_to_use: "Second"
    usage: "tool-b --help"
EOF

  bash "$GENERATOR"
  grep -q "### tool-a" "$TOOL_CONTEXT_OUTPUT"
  grep -q "### tool-b" "$TOOL_CONTEXT_OUTPUT"
}

# ── Visibility tiers ─────────────────────────────────────────────────────────

@test "visibility: full renders full entry" {
  _write_visibility_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q "### full-tool" "$TOOL_CONTEXT_OUTPUT"
  grep -q "When to use" "$TOOL_CONTEXT_OUTPUT"
}

@test "visibility: brief renders one-liner" {
  _write_visibility_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  grep -q '^\- \*\*ref-tool\*\*' "$TOOL_CONTEXT_OUTPUT"
  run grep "### ref-tool" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "visibility: hidden omits tool entirely" {
  _write_visibility_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  run grep "hidden-tool" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "output file has no frontmatter" {
  _write_registry "$BREW_REGISTRY"

  bash "$GENERATOR"
  run grep "^---" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

# ── Scoped output ────────────────────────────────────────────────────────────

# _write_scoped_registry FILE SECTION SCOPE — writes a registry with meta.scope
_write_scoped_registry() {
  local file="$1" section="${2:-Tools}" scope="$3"
  cat > "$file" << EOF
meta:
  section: "$section"
  scope: "$scope"
  install_check: false
  validation: none

tools:
  - name: scoped-tool
    permission: false
    visibility: brief
    description: "A scoped tool"
EOF
}

@test "scoped registry writes to tools.generated.<scope>.md" {
  _write_scoped_registry "$WORK_DIR/go.registry.yml" "Go Tools" "go"

  bash "$GENERATOR"
  local scoped_file
  scoped_file="$(dirname "$TOOL_CONTEXT_OUTPUT")/tools.generated.go.md"
  [ -f "$scoped_file" ]
  grep -q "Go Tools" "$scoped_file"
}

@test "scoped registry does not appear in core output" {
  _write_registry "$BREW_REGISTRY" "Core Tools"
  _write_scoped_registry "$WORK_DIR/go.registry.yml" "Go Tools" "go"

  bash "$GENERATOR"
  grep -q "Core Tools" "$TOOL_CONTEXT_OUTPUT"
  run grep "Go Tools" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "scoped output file has paths frontmatter" {
  _write_scoped_registry "$WORK_DIR/go.registry.yml" "Go Tools" "go"

  bash "$GENERATOR"
  local scoped_file
  scoped_file="$(dirname "$TOOL_CONTEXT_OUTPUT")/tools.generated.go.md"
  grep -q "^---" "$scoped_file"
  grep -q "paths:" "$scoped_file"
  grep -q '"\*\*/\*.go"' "$scoped_file"
}

@test "multiple registries with same scope merge into one file" {
  _write_scoped_registry "$WORK_DIR/aws.registry.yml" "AWS Tools" "infra"

  cat > "$WORK_DIR/k8s.registry.yml" << 'EOF'
meta:
  section: "Kubernetes Tools"
  scope: infra
  install_check: false
  validation: none

tools:
  - name: kubectl
    permission: false
    visibility: brief
    description: "Kubernetes CLI"
EOF

  bash "$GENERATOR"
  local scoped_file
  scoped_file="$(dirname "$TOOL_CONTEXT_OUTPUT")/tools.generated.infra.md"
  [ -f "$scoped_file" ]
  grep -q "AWS Tools" "$scoped_file"
  grep -q "Kubernetes Tools" "$scoped_file"
}

@test "stale scope files are cleaned up" {
  local stale_file
  stale_file="$(dirname "$TOOL_CONTEXT_OUTPUT")/tools.generated.oldscope.md"
  echo "stale" > "$stale_file"

  _write_registry "$BREW_REGISTRY"
  bash "$GENERATOR"
  [ ! -f "$stale_file" ]
}

@test "unknown scope exits non-zero" {
  _write_scoped_registry "$WORK_DIR/python.registry.yml" "Python Tools" "python"

  run bash "$GENERATOR"
  [ "$status" -ne 0 ]
}

@test "core scope is treated as unscoped" {
  cat > "$WORK_DIR/core.registry.yml" << 'EOF'
meta:
  section: "Core Extra"
  scope: core
  install_check: false
  validation: none

tools:
  - name: core-tool
    permission: false
    visibility: brief
    description: "Explicit core scope tool"
EOF

  bash "$GENERATOR"
  grep -q "Core Extra" "$TOOL_CONTEXT_OUTPUT"
  local scoped_file
  scoped_file="$(dirname "$TOOL_CONTEXT_OUTPUT")/tools.generated.core.md"
  [ ! -f "$scoped_file" ]
}
