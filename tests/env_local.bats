#!/usr/bin/env bats
# Validates the .env.local setup: loader sourcing, template existence, and
# the bootstrap step in zsh/steps.sh.

setup() {
  REPO_ROOT="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"
}

# ── loader.zsh sources .env.local ─────────────────────────────────────────────

@test "loader.zsh contains .env.local source line" {
  grep -q 'env\.local' "$REPO_ROOT/zsh/config.d/loader.zsh"
}

@test ".env.local is sourced before _wb_load framework in loader.zsh" {
  local loader="$REPO_ROOT/zsh/config.d/loader.zsh"
  local env_line fw_line
  env_line=$(grep -n 'env\.local' "$loader" | head -1 | cut -d: -f1)
  fw_line=$(grep -n '_wb_load framework' "$loader" | head -1 | cut -d: -f1)
  [ -n "$env_line" ]
  [ -n "$fw_line" ]
  [ "$env_line" -lt "$fw_line" ]
}

@test ".env.local source line is guarded with a file existence check" {
  grep -qE '\[\[.*-f.*env\.local.*\]\]' "$REPO_ROOT/zsh/config.d/loader.zsh"
}

# ── template ──────────────────────────────────────────────────────────────────

@test ".env.local template exists" {
  [ -f "$REPO_ROOT/zsh/.env.local.template" ]
}

@test ".env.local template is non-empty" {
  local lines
  lines=$(wc -l < "$REPO_ROOT/zsh/.env.local.template")
  [ "$lines" -gt 5 ]
}

@test ".env.local template documents the taskfile.env distinction" {
  grep -q 'taskfile.env' "$REPO_ROOT/zsh/.env.local.template"
}

# ── bootstrap step ────────────────────────────────────────────────────────────

@test "zsh/steps.sh defines _env_local_bootstrap function" {
  grep -q '_env_local_bootstrap' "$REPO_ROOT/zsh/steps.sh"
}

@test "_env_local_bootstrap creates ~/.env.local from template when absent" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; _env_local_bootstrap" \
    >/dev/null 2>&1

  [ -f "$FAKE_HOME/.env.local" ]
  rm -rf "$TMPDIR"
}

@test "_env_local_bootstrap does not overwrite existing ~/.env.local" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"
  echo "export MY_CUSTOM_VAR=keep_me" > "$FAKE_HOME/.env.local"

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; _env_local_bootstrap" \
    >/dev/null 2>&1

  grep -q 'MY_CUSTOM_VAR=keep_me' "$FAKE_HOME/.env.local"
  rm -rf "$TMPDIR"
}

# ── registry-driven env sync ─────────────────────────────────────────────────

# _run_bootstrap FAKE_HOME [WORKBENCH_DIR] — runs _env_local_bootstrap in isolation.
_run_bootstrap() {
  local fake_home="$1"
  local wb="${2:-$REPO_ROOT}"
  HOME="$fake_home" WORKBENCH_DIR="$wb" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$wb/zsh/steps.sh'; _env_local_bootstrap" \
    2>/dev/null
}

@test "_env_local_bootstrap adds missing auth vars from registries" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_WB="$TMPDIR/wb"
  mkdir -p "$FAKE_HOME" "$FAKE_WB/zsh" "$FAKE_WB/lib" "$FAKE_WB/tools"

  cp "$REPO_ROOT/lib/ui.sh" "$FAKE_WB/lib/"
  cp "$REPO_ROOT/lib/constants.sh" "$FAKE_WB/lib/"
  cp "$REPO_ROOT/lib/registries.sh" "$FAKE_WB/lib/"
  cp "$REPO_ROOT/zsh/steps.sh" "$FAKE_WB/zsh/"
  cp "$REPO_ROOT/zsh/.env.local.template" "$FAKE_WB/zsh/"

  cat > "$FAKE_WB/tools/registry.yml" << 'EOF'
meta:
  section: "Test Tools"
  install_check: false
  validation: none

tools:
  - name: testtool
    description: "A test tool"
    when_to_use: "Testing"
    auth:
      env_var: TEST_API_KEY
      setup_url: "https://example.com/keys"
      prefix: "tk_"
EOF

  echo "# existing content" > "$FAKE_HOME/.env.local"

  _run_bootstrap "$FAKE_HOME" "$FAKE_WB"

  grep -q 'TEST_API_KEY' "$FAKE_HOME/.env.local"
  grep -q 'tk_' "$FAKE_HOME/.env.local"
  grep -q '# existing content' "$FAKE_HOME/.env.local"
  rm -rf "$TMPDIR"
}

@test "_env_local_bootstrap skips vars already present in env.local" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_WB="$TMPDIR/wb"
  mkdir -p "$FAKE_HOME" "$FAKE_WB/zsh" "$FAKE_WB/lib" "$FAKE_WB/tools"

  cp "$REPO_ROOT/lib/ui.sh" "$FAKE_WB/lib/"
  cp "$REPO_ROOT/lib/constants.sh" "$FAKE_WB/lib/"
  cp "$REPO_ROOT/lib/registries.sh" "$FAKE_WB/lib/"
  cp "$REPO_ROOT/zsh/steps.sh" "$FAKE_WB/zsh/"
  cp "$REPO_ROOT/zsh/.env.local.template" "$FAKE_WB/zsh/"

  cat > "$FAKE_WB/tools/registry.yml" << 'EOF'
meta:
  section: "Test Tools"
  install_check: false
  validation: none

tools:
  - name: testtool
    description: "A test tool"
    when_to_use: "Testing"
    auth:
      env_var: TEST_API_KEY
      setup_url: "https://example.com/keys"
EOF

  echo "export TEST_API_KEY=already_set" > "$FAKE_HOME/.env.local"

  _run_bootstrap "$FAKE_HOME" "$FAKE_WB"

  local count
  count=$(grep -c 'TEST_API_KEY' "$FAKE_HOME/.env.local")
  [ "$count" -eq 1 ]
  grep -q 'already_set' "$FAKE_HOME/.env.local"
  rm -rf "$TMPDIR"
}

@test "_env_local_bootstrap skips commented vars already present" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_WB="$TMPDIR/wb"
  mkdir -p "$FAKE_HOME" "$FAKE_WB/zsh" "$FAKE_WB/lib" "$FAKE_WB/tools"

  cp "$REPO_ROOT/lib/ui.sh" "$FAKE_WB/lib/"
  cp "$REPO_ROOT/lib/constants.sh" "$FAKE_WB/lib/"
  cp "$REPO_ROOT/lib/registries.sh" "$FAKE_WB/lib/"
  cp "$REPO_ROOT/zsh/steps.sh" "$FAKE_WB/zsh/"
  cp "$REPO_ROOT/zsh/.env.local.template" "$FAKE_WB/zsh/"

  cat > "$FAKE_WB/tools/registry.yml" << 'EOF'
meta:
  section: "Test Tools"
  install_check: false
  validation: none

tools:
  - name: testtool
    description: "A test tool"
    when_to_use: "Testing"
    auth:
      env_var: TEST_API_KEY
      setup_url: "https://example.com/keys"
EOF

  echo "# export TEST_API_KEY=" > "$FAKE_HOME/.env.local"

  _run_bootstrap "$FAKE_HOME" "$FAKE_WB"

  local count
  count=$(grep -c 'TEST_API_KEY' "$FAKE_HOME/.env.local")
  [ "$count" -eq 1 ]
  rm -rf "$TMPDIR"
}
