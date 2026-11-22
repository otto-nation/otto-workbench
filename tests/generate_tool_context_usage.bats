#!/usr/bin/env bats
# The usage line of a rendered entry: when it appears, and the usage_mode: help form.

setup_file() {
  load 'test_helper'
}

setup() {
  load 'test_helper'
  load 'generate_tool_context_helper'
  common_setup
  source "$REPO_ROOT/bin/local/generate-tool-context"
  gtc_setup
}

teardown() {
  gtc_teardown
  common_teardown
}

@test "renders usage field when present" {
  _write_registry "$PACKAGES_REGISTRY"

  main
  grep -q "mytool --flag" "$TOOL_CONTEXT_OUTPUT"
}

@test "omits usage line for visibility: brief entry" {
  cat > "$PACKAGES_REGISTRY" << 'EOF'
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

@test "usage_mode: help keeps description and when_to_use but drops the flag list" {
  cat > "$PACKAGES_REGISTRY" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

tools:
  - name: heavytool
    permission: false
    visibility: full
    usage_mode: help
    description: "A tool with many flags"
    when_to_use: "When the flags matter"
    usage: "heavytool --alpha --beta-flag-unique"
    commands:
      - name: "sub1 [--opt]"
        description: "First subcommand"
      - name: "sub2 ARG"
        description: "Second subcommand"
      - name: "sub2 other"
        description: "Second again"
EOF

  main
  grep -qF '### heavytool' "$TOOL_CONTEXT_OUTPUT"
  grep -qF 'A tool with many flags' "$TOOL_CONTEXT_OUTPUT"
  grep -qF '**When to use**: When the flags matter' "$TOOL_CONTEXT_OUTPUT"
  grep -qF 'run `heavytool --help` for flags' "$TOOL_CONTEXT_OUTPUT"
  grep -qxF -e '- **Subcommands**: `sub1`, `sub2`' "$TOOL_CONTEXT_OUTPUT"
  run grep -E -e '--beta-flag-unique|--opt|First subcommand' "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}

@test "without usage_mode the flag list is still rendered" {
  _write_registry "$PACKAGES_REGISTRY"

  main
  grep -qF '`mytool --flag`' "$TOOL_CONTEXT_OUTPUT"
  run grep -F -e '--help` for flags' "$TOOL_CONTEXT_OUTPUT"
  [ "$status" -ne 0 ]
}
