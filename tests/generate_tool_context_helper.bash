# Shared helper for the generate-tool-context test files.
# Loaded in setup() after test_helper, so common_setup has pinned $TMPDIR.

# Point every generator input and output at temp paths, so a test never
# touches real workbench files (registry data, tools.generated.md). Each suite
# sources the generator itself: bin/local/select-tests maps a suite to what it
# covers by the path literals in the .bats file, not in its helpers.
gtc_setup() {
  ORIG_DIR="$PWD"

  mkdir -p "$TMPDIR/packages" "$TMPDIR/bin" "$TMPDIR/zsh" "$TMPDIR/mise"
  export PACKAGES_REGISTRY="$TMPDIR/packages/registry.yml"
  export MISE_REGISTRY="$TMPDIR/mise/registry.yml"
  export BIN_REGISTRY="$TMPDIR/bin/registry.yml"
  export ZSH_REGISTRY="$TMPDIR/zsh/registry.yml"
  export PACKAGES_STACKS_DIR="$TMPDIR"
  export WORK_DIR="$TMPDIR/work"
  export TOOL_CONTEXT_OUTPUT="$TMPDIR/tools.generated.md"
  export AI_DIR="$TMPDIR/ai"
  export REGISTRY_SCAN_DIR="$TMPDIR"

  mkdir -p "$WORK_DIR"
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

gtc_teardown() {
  cd "$ORIG_DIR" || return 1
  unset PACKAGES_REGISTRY MISE_REGISTRY BIN_REGISTRY ZSH_REGISTRY PACKAGES_STACKS_DIR WORK_DIR TOOL_CONTEXT_OUTPUT REGISTRY_SCAN_DIR AI_DIR
}
