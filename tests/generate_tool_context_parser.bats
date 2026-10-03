#!/usr/bin/env bats
# Usage rendered from an entry's argument parser rather than written by hand.

setup_file() {
  load 'test_helper'
}

setup() {
  load 'test_helper'
  load 'generate_tool_context_helper'
  common_setup
  gtc_setup
}

teardown() {
  gtc_teardown
  common_teardown
}

@test "renders the usage of an entry with a parser from that parser" {
  # The usage agents read is rendered, not written: a flag the parser gains
  # reaches tools.generated.md with no second edit.
  cat > "$BIN_REGISTRY" << 'EOF'
meta:
  section: "Workbench Scripts"
  validation: bindir
  source: bin

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A test tool"
    when_to_use: "When testing"
    parser: bin/mytool:build_parser
EOF
  cat > "$TMPDIR/bin/mytool" << 'EOF'
#!/usr/bin/env python3
import argparse
def build_parser():
    p = argparse.ArgumentParser(prog="mytool")
    p.add_argument("--onto", "--base", metavar="REF", help="Ref")
    return p
EOF
  chmod +x "$TMPDIR/bin/mytool"

  main
  grep -qF -- '- **Usage**: `mytool [--onto|--base <ref>]`' "$TOOL_CONTEXT_OUTPUT"
}

@test "a parser that does not load stops the run instead of dropping the usage" {
  cat > "$BIN_REGISTRY" << 'EOF'
meta:
  section: "Workbench Scripts"
  validation: bindir
  source: bin

tools:
  - name: mytool
    permission: false
    visibility: full
    description: "A test tool"
    when_to_use: "When testing"
    parser: bin/missing:build_parser
EOF

  run main
  [ "$status" -ne 0 ]
  [[ "$output" == *"bin/missing"* ]]
}
