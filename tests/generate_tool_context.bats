#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
}

setup() {
  load 'test_helper'
  common_setup
  source "$REPO_ROOT/bin/local/generate-tool-context"
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
}

teardown() {
  cd "$ORIG_DIR" || return 1
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

# _write_visibility_registry FILE SECTION — writes a registry with tools at different visibility tiers
_write_visibility_registry() {
  local file="$1" section="${2:-Visibility Tools}"
  cat > "$file" << EOF
meta:
  section: "$section"
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

  run main
  [ "$status" -eq 0 ]
  [ -f "$TOOL_CONTEXT_OUTPUT" ]
}

@test "output contains auto-generated header comment" {
  _write_registry "$BREW_REGISTRY"

  main
  grep -q "AUTO-GENERATED" "$TOOL_CONTEXT_OUTPUT"
}

# ── Section rendering ─────────────────────────────────────────────────────────

@test "renders section title from meta.section in BREW_REGISTRY" {
  _write_registry "$BREW_REGISTRY" "Brew Tools"

  main
  grep -q "## Brew Tools" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders section title from meta.section in BIN_REGISTRY" {
  _write_registry "$BIN_REGISTRY" "Workbench Scripts"

  main
  grep -q "## Workbench Scripts" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders section title from meta.section in ZSH_REGISTRY" {
  _write_registry "$ZSH_REGISTRY" "Shell Aliases"

  main
  grep -q "## Shell Aliases" "$TOOL_CONTEXT_OUTPUT"
}

# ── Tool entry fields ─────────────────────────────────────────────────────────

@test "renders tool name as H3" {
  _write_registry "$BREW_REGISTRY"

  main
  grep -q "### mytool" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders tool description" {
  _write_registry "$BREW_REGISTRY"

  main
  grep -q "A test tool" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders when_to_use field" {
  _write_registry "$BREW_REGISTRY"

  main
  grep -q "When testing" "$TOOL_CONTEXT_OUTPUT"
}

@test "renders usage field when present" {
  _write_registry "$BREW_REGISTRY"

  main
  grep -q "mytool --flag" "$TOOL_CONTEXT_OUTPUT"
}

@test "omits docs field from output" {
  _write_registry "$BREW_REGISTRY"

  main
  run grep "https://example.com" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "omits usage line for visibility: brief entry" {
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Tools"
  validation: none

tools:
  - name: brief-tool
    permission: false
    visibility: brief
    description: "A brief tool"
EOF

  main
  run grep "Usage" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "omits docs line when docs is absent" {
  _write_minimal_registry "$BREW_REGISTRY"

  main
  run grep "Docs" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

# ── Missing registries ────────────────────────────────────────────────────────

@test "succeeds when all registries are missing" {
  run main
  [ "$status" -eq 0 ]
}

@test "skips section for missing registry file" {
  _write_registry "$BREW_REGISTRY" "Brew Tools"

  main
  grep -q "## Brew Tools" "$TOOL_CONTEXT_OUTPUT"
  run grep "## Workbench Scripts" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

# ── Multiple entries ──────────────────────────────────────────────────────────

@test "renders multiple tool entries" {
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Tools"
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

  main
  grep -q "### tool-a" "$TOOL_CONTEXT_OUTPUT"
  grep -q "### tool-b" "$TOOL_CONTEXT_OUTPUT"
}

# ── Section deduplication ────────────────────────────────────────────────────

@test "multiple registries with same section share one header" {
  cat > "$WORK_DIR/a.registry.yml" << 'EOF'
meta:
  section: "Shared Section"
  validation: none

tools:
  - name: tool-a
    permission: false
    visibility: full
    description: "Tool A"
    when_to_use: "Always"
    usage: "tool-a --help"
EOF
  cat > "$WORK_DIR/b.registry.yml" << 'EOF'
meta:
  section: "Shared Section"
  validation: none

tools:
  - name: tool-b
    permission: false
    visibility: brief
    description: "Tool B"
EOF

  main
  grep -q "### tool-a" "$TOOL_CONTEXT_OUTPUT"
  grep -q "tool-b" "$TOOL_CONTEXT_OUTPUT"
  local count
  count=$(grep -c "## Shared Section" "$TOOL_CONTEXT_OUTPUT")
  [ "$count" -eq 1 ]
}

@test "brief entries render before full entries across shared-section registries" {
  cat > "$WORK_DIR/a.registry.yml" << 'EOF'
meta:
  section: "Shared Section"
  validation: none

tools:
  - name: full-tool
    permission: false
    visibility: full
    description: "Full entry"
    when_to_use: "Always"
    usage: "full-tool --run"
EOF
  cat > "$WORK_DIR/b.registry.yml" << 'EOF'
meta:
  section: "Shared Section"
  validation: none

tools:
  - name: brief-tool
    permission: false
    visibility: brief
    description: "Brief entry"
EOF

  main
  local brief_line full_line
  brief_line=$(grep -n "brief-tool" "$TOOL_CONTEXT_OUTPUT" | head -1 | cut -d: -f1)
  full_line=$(grep -n "### full-tool" "$TOOL_CONTEXT_OUTPUT" | head -1 | cut -d: -f1)
  [ "$brief_line" -lt "$full_line" ]
}

# ── Visibility tiers ─────────────────────────────────────────────────────────

@test "visibility: full renders full entry" {
  _write_visibility_registry "$BREW_REGISTRY"

  main
  grep -q "### full-tool" "$TOOL_CONTEXT_OUTPUT"
  grep -q "When to use" "$TOOL_CONTEXT_OUTPUT"
}

@test "visibility: brief renders one-liner" {
  _write_visibility_registry "$BREW_REGISTRY"

  main
  grep -q '^\- \*\*ref-tool\*\*' "$TOOL_CONTEXT_OUTPUT"
  run grep "### ref-tool" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "visibility: hidden omits tool entirely" {
  _write_visibility_registry "$BREW_REGISTRY"

  main
  run grep "hidden-tool" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

# ── Commands rendering ────────────────────────────────────────────────────────

@test "renders subcommands for tool with commands field" {
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A tool with subcommands"
    when_to_use: "When testing"
    usage: "mytool sub1 | mytool sub2"
    commands:
      - name: sub1
        description: "First subcommand"
      - name: sub2
        description: "Second subcommand"
EOF

  main
  grep -q "Subcommands" "$TOOL_CONTEXT_OUTPUT"
  grep -q '`sub1` — First subcommand' "$TOOL_CONTEXT_OUTPUT"
  grep -q '`sub2` — Second subcommand' "$TOOL_CONTEXT_OUTPUT"
}

@test "omits subcommands section when commands field is absent" {
  _write_registry "$BREW_REGISTRY"

  main
  run grep "Subcommands" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

# ── Output format ────────────────────────────────────────────────────────────

@test "output file has no frontmatter" {
  _write_registry "$BREW_REGISTRY"

  main
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

  main
  local scoped_file
  scoped_file="$(dirname "$TOOL_CONTEXT_OUTPUT")/tools.generated.go.md"
  [ -f "$scoped_file" ]
  grep -q "Go Tools" "$scoped_file"
}

@test "scoped registry does not appear in core output" {
  _write_registry "$BREW_REGISTRY" "Core Tools"
  _write_scoped_registry "$WORK_DIR/go.registry.yml" "Go Tools" "go"

  main
  grep -q "Core Tools" "$TOOL_CONTEXT_OUTPUT"
  run grep "Go Tools" "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "scoped output file has paths frontmatter" {
  _write_scoped_registry "$WORK_DIR/go.registry.yml" "Go Tools" "go"

  main
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
  validation: none

tools:
  - name: kubectl
    permission: false
    visibility: brief
    description: "Kubernetes CLI"
EOF

  main
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
  main
  [ ! -f "$stale_file" ]
}

@test "unknown scope exits non-zero" {
  _write_scoped_registry "$WORK_DIR/python.registry.yml" "Python Tools" "python"

  run main
  [ "$status" -ne 0 ]
}

@test "core scope is treated as unscoped" {
  cat > "$WORK_DIR/core.registry.yml" << 'EOF'
meta:
  section: "Core Extra"
  scope: core
  validation: none

tools:
  - name: core-tool
    permission: false
    visibility: brief
    description: "Explicit core scope tool"
EOF

  main
  grep -q "Core Extra" "$TOOL_CONTEXT_OUTPUT"
  local scoped_file
  scoped_file="$(dirname "$TOOL_CONTEXT_OUTPUT")/tools.generated.core.md"
  [ ! -f "$scoped_file" ]
}

# ── docs/libraries.md module tables ──────────────────────────────────────────

# _lib_fixture — a temp lib/ and libraries.md the generator will read instead
# of the real ones. Both are the env hooks main() resolves through.
_lib_fixture() {
  export LIB_DIR="$TMPDIR/lib"
  export LIBRARIES_DOC_PATH="$TMPDIR/docs/libraries.md"
  mkdir -p "$LIB_DIR/ai"
  : > "$LIBRARIES_DOC_PATH"
}

# _lib_markers REL — the splice markers for one module, as the doc holds them.
_lib_markers() {
  printf '<!-- LIB-FUNCTIONS:%s-START -->\n<!-- LIB-FUNCTIONS:%s-END -->\n' "$1" "$1" \
    >> "$LIBRARIES_DOC_PATH"
}

@test "lib doc rows split signature from purpose on the em dash" {
  _lib_fixture
  cat > "$LIB_DIR/output.sh" << 'EOF'
# info MESSAGE — blue info message with an arrow.
info() { echo "$*"; }
EOF
  run _lib_doc_rows "$LIB_DIR/output.sh"
  [ "$output" = "$(printf 'info MESSAGE\tblue info message with an arrow.')" ]
}

@test "lib doc rows take the purpose from the description when the signature stands alone" {
  _lib_fixture
  cat > "$LIB_DIR/menu.sh" << 'EOF'
# select_menu RESULT_VAR COUNT
#
# Displays a numbered selection prompt.
select_menu() { :; }
EOF
  run _lib_doc_rows "$LIB_DIR/menu.sh"
  [ "$output" = "$(printf 'select_menu RESULT_VAR COUNT\tDisplays a numbered selection prompt.')" ]
}

@test "lib doc rows join a wrapped purpose and stop at the end of the sentence" {
  _lib_fixture
  cat > "$LIB_DIR/portable.sh" << 'EOF'
# file_birth PATH — birth time in epoch seconds. Prints 0 on
# filesystems that do not record one. Callers must treat 0 as unknown.
file_birth() { :; }
EOF
  run _lib_doc_rows "$LIB_DIR/portable.sh"
  [ "$output" = "$(printf 'file_birth PATH\tbirth time in epoch seconds. Prints 0 on filesystems that do not record one.')" ]
}

@test "lib doc rows pair a comment with its function across intervening declarations" {
  _lib_fixture
  cat > "$LIB_DIR/install.sh" << 'EOF'
# resolve_known_components ARRAY_REF — resolves component names to paths.
declare -a KNOWN_COMPONENTS=()
declare -A COMPONENT_PATHS=()
resolve_known_components() { :; }
EOF
  run _lib_doc_rows "$LIB_DIR/install.sh"
  [ "$output" = "$(printf 'resolve_known_components ARRAY_REF\tresolves component names to paths.')" ]
}

@test "lib functions table skips private functions and escapes pipes" {
  _lib_fixture
  cat > "$LIB_DIR/menu.sh" << 'EOF'
# select_menu RESULT_VAR [--default all|skip] — numbered selection prompt.
select_menu() { :; }

# _render_row INDEX — internal.
_render_row() { :; }
EOF
  run _lib_functions_table "$LIB_DIR/menu.sh"
  [[ "$output" == *'| `select_menu RESULT_VAR [--default all\|skip]` | numbered selection prompt. |'* ]]
  [[ "$output" != *_render_row* ]]
}

@test "lib roots table reads the constant, XDG rung and default off the assignment" {
  _lib_fixture
  cat > "$LIB_DIR/roots.sh" << 'EOF'
# Hand-authored settings: config.yml, overrides/.
WORKBENCH_CONFIG_DIR="$(_wb_root "${WORKBENCH_CONFIG_DIR:-}" "${XDG_CONFIG_HOME:-}" "$HOME/.config/workbench")"
EOF
  run _lib_roots_table
  [[ "$output" == *'| `WORKBENCH_CONFIG_DIR` | Hand-authored settings: config.yml, overrides/ | `XDG_CONFIG_HOME` | `~/.config/workbench` |'* ]]
}

@test "lib doc check fails on a public function with no doc comment" {
  _lib_fixture
  _lib_markers "output.sh"
  cat > "$LIB_DIR/output.sh" << 'EOF'
# info MESSAGE — blue info message with an arrow.
info() { echo "$*"; }

warn() { echo "$*"; }
EOF
  run _lib_check_docs
  [ "$status" -ne 0 ]
  [[ "$output" == *"lib/output.sh: warn has no doc comment"* ]]
}

@test "lib doc check fails when a module has no section in the doc" {
  _lib_fixture
  cat > "$LIB_DIR/output.sh" << 'EOF'
# info MESSAGE — blue info message with an arrow.
info() { echo "$*"; }
EOF
  run _lib_check_docs
  [ "$status" -ne 0 ]
  [[ "$output" == *"no section for lib/output.sh"* ]]
}

@test "lib doc check passes when every public function is documented and placed" {
  _lib_fixture
  _lib_markers "output.sh"
  cat > "$LIB_DIR/output.sh" << 'EOF'
# info MESSAGE — blue info message with an arrow.
info() { echo "$*"; }

# _shout MESSAGE — internal, needs no section.
_shout() { echo "$*"; }
EOF
  run _lib_check_docs
  [ "$status" -eq 0 ]
}

@test "libraries doc splice renders each module table between its markers" {
  _lib_fixture
  cat > "$LIB_DIR/output.sh" << 'EOF'
# info MESSAGE — blue info message with an arrow.
info() { echo "$*"; }
EOF
  cat > "$LIB_DIR/ai/pr.sh" << 'EOF'
# load_pr_context PR_NUMBER — fetches PR metadata into the environment.
load_pr_context() { :; }
EOF
  cat > "$LIB_DIR/roots.sh" << 'EOF'
# Recomputable data, safe to delete at any time.
WORKBENCH_CACHE_DIR="$(_wb_root "${WORKBENCH_CACHE_DIR:-}" "${XDG_CACHE_HOME:-}" "$HOME/.cache/workbench")"
EOF
  cat > "$LIBRARIES_DOC_PATH" << 'EOF'
### output.sh
<!-- LIB-FUNCTIONS:output.sh-START -->
<!-- LIB-FUNCTIONS:output.sh-END -->

### ai/pr.sh
<!-- LIB-FUNCTIONS:ai/pr.sh-START -->
<!-- LIB-FUNCTIONS:ai/pr.sh-END -->

## Roots
<!-- LIB-ROOTS-START -->
<!-- LIB-ROOTS-END -->
EOF

  _splice_libraries_doc

  grep -qF '| `info MESSAGE` | blue info message with an arrow. |' "$LIBRARIES_DOC_PATH"
  grep -qF '| `load_pr_context PR_NUMBER` | fetches PR metadata into the environment. |' "$LIBRARIES_DOC_PATH"
  grep -qF '| `WORKBENCH_CACHE_DIR` |' "$LIBRARIES_DOC_PATH"
  # Prose around the blocks is untouched, and roots.sh has no function table.
  grep -qF '### output.sh' "$LIBRARIES_DOC_PATH"
  [ "$(grep -c '^| `' "$LIBRARIES_DOC_PATH")" -eq 3 ]
}
