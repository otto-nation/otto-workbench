#!/usr/bin/env bats

setup() {
  load 'test_helper'
  ORIG_DIR="$PWD"
  TMPDIR="$(mktemp -d)"

  # Point all generator inputs/outputs at temp paths so tests never touch
  # real workbench files (registry data, tools.generated.md, README.md).
  mkdir -p "$TMPDIR/brew" "$TMPDIR/bin" "$TMPDIR/zsh"
  export BREW_REGISTRY="$TMPDIR/brew/registry.yml"
  export BIN_REGISTRY="$TMPDIR/bin/registry.yml"
  export ZSH_REGISTRY="$TMPDIR/zsh/registry.yml"
  export BREW_STACKS_DIR="$TMPDIR"
  export WORK_DIR="$TMPDIR/work"
  export TOOL_CONTEXT_OUTPUT="$TMPDIR/tools.generated.md"
  export README_PATH="$TMPDIR/README.md"
  export TASKFILE_PATH="$TMPDIR/Taskfile.yml"
  export AI_DIR="$TMPDIR/ai"
  export REGISTRY_SCAN_DIR="$TMPDIR"
  export ENV_LOCAL_TEMPLATE_PATH="$TMPDIR/env.local.template.nonexistent"

  mkdir -p "$WORK_DIR"
  GENERATOR="$REPO_ROOT/bin/generate-tool-context"
}

teardown() {
  cd "$ORIG_DIR"
  rm -rf "$TMPDIR"
  unset BREW_REGISTRY BIN_REGISTRY ZSH_REGISTRY BREW_STACKS_DIR WORK_DIR TOOL_CONTEXT_OUTPUT ENV_LOCAL_TEMPLATE_PATH REGISTRY_SCAN_DIR
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

# ── Env section generation ───────────────────────────────────────────────────

# _write_env_template — creates a minimal .env.local.template with ENV markers
_write_env_template() {
  export ENV_LOCAL_TEMPLATE_PATH="$TMPDIR/env.local.template"
  cat > "$ENV_LOCAL_TEMPLATE_PATH" << 'EOF'
# header
# --- ENV-START ---
# --- ENV-END ---
# footer
EOF
}

# _write_auth_registry FILE — writes a registry with an auth block
_write_auth_registry() {
  local file="$1"
  cat > "$file" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

tools:
  - name: mytool
    description: "A test tool"
    when_to_use: "When testing"
    auth:
      env_var: MY_API_KEY
      setup_url: "https://example.com/api-keys"
EOF
}

# _write_env_registry FILE — writes a registry with a top-level env block
_write_env_registry() {
  local file="$1"
  cat > "$file" << 'EOF'
meta:
  section: "Config"
  install_check: false
  validation: none

env:
  - var: MY_CONFIG_VAR
    comment: "A config variable"
    default: "mydefault"

tools: []
EOF
}

@test "generates auth entry in env template" {
  _write_auth_registry "$BREW_REGISTRY"
  _write_env_template

  bash "$GENERATOR"
  grep -q "MY_API_KEY" "$ENV_LOCAL_TEMPLATE_PATH"
}

@test "auth entry includes setup_url as comment" {
  _write_auth_registry "$BREW_REGISTRY"
  _write_env_template

  bash "$GENERATOR"
  grep -q "https://example.com/api-keys" "$ENV_LOCAL_TEMPLATE_PATH"
}

@test "auth entry renders prefix when present" {
  _write_env_template
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

tools:
  - name: mytool
    description: "A test tool"
    when_to_use: "When testing"
    auth:
      env_var: MY_API_KEY
      prefix: "mk_"
      setup_url: "https://example.com"
EOF

  bash "$GENERATOR"
  grep -q "MY_API_KEY=mk_" "$ENV_LOCAL_TEMPLATE_PATH"
}

@test "no env section when tools have no auth or env block" {
  _write_registry "$BREW_REGISTRY"
  _write_env_template

  bash "$GENERATOR"
  run grep "export" "$ENV_LOCAL_TEMPLATE_PATH"
  [ "$status" -ne 0 ]
}

@test "env preserves surrounding content in template" {
  _write_auth_registry "$BREW_REGISTRY"
  _write_env_template

  bash "$GENERATOR"
  grep -q "# header" "$ENV_LOCAL_TEMPLATE_PATH"
  grep -q "# footer" "$ENV_LOCAL_TEMPLATE_PATH"
}

# ── Top-level env entries ────────────────────────────────────────────────────

@test "env entry renders var with default value" {
  _write_env_registry "$BREW_REGISTRY"
  _write_env_template

  bash "$GENERATOR"
  grep -q "MY_CONFIG_VAR=mydefault" "$ENV_LOCAL_TEMPLATE_PATH"
}

@test "env entry renders comment above export" {
  _write_env_registry "$BREW_REGISTRY"
  _write_env_template

  bash "$GENERATOR"
  grep -q "# A config variable" "$ENV_LOCAL_TEMPLATE_PATH"
}

@test "env entry renders setup_url in comment" {
  _write_env_template
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

env:
  - var: MY_KEY
    comment: "API key"
    setup_url: "https://example.com/keys"

tools: []
EOF

  bash "$GENERATOR"
  grep -q "https://example.com/keys" "$ENV_LOCAL_TEMPLATE_PATH"
}

@test "env entry renders prefix over default" {
  _write_env_template
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Tools"
  install_check: false
  validation: none

env:
  - var: MY_TOKEN
    prefix: "tok_"
    default: "should-not-appear"

tools: []
EOF

  bash "$GENERATOR"
  grep -q "MY_TOKEN=tok_" "$ENV_LOCAL_TEMPLATE_PATH"
  run grep "should-not-appear" "$ENV_LOCAL_TEMPLATE_PATH"
  [ "$status" -ne 0 ]
}

@test "env section renders section header" {
  _write_env_registry "$BREW_REGISTRY"
  _write_env_template

  bash "$GENERATOR"
  grep -q "Config" "$ENV_LOCAL_TEMPLATE_PATH"
}

# ── Install-check filtering for env/auth ─────────────────────────────────────

@test "env section skipped when install_check_command not found" {
  _write_env_template
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Filtered"
  install_check: true
  install_check_command: definitely-not-a-real-tool-xyzzy
  validation: none

env:
  - var: SHOULD_NOT_APPEAR
    default: "hidden"

tools: []
EOF

  bash "$GENERATOR"
  run grep "SHOULD_NOT_APPEAR" "$ENV_LOCAL_TEMPLATE_PATH"
  [ "$status" -ne 0 ]
}

@test "auth entry skipped when tool not installed and install_check is true" {
  _write_env_template
  cat > "$BREW_REGISTRY" << 'EOF'
meta:
  section: "Tools"
  install_check: true
  validation: none

tools:
  - name: definitely-not-a-real-tool-xyzzy
    description: "Missing tool"
    when_to_use: "Never"
    auth:
      env_var: SHOULD_NOT_APPEAR
      setup_url: "https://example.com"
EOF

  bash "$GENERATOR"
  run grep "SHOULD_NOT_APPEAR" "$ENV_LOCAL_TEMPLATE_PATH"
  [ "$status" -ne 0 ]
}
