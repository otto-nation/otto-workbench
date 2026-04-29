#!/usr/bin/env bats
# Validates the .env.local setup: loader sourcing, template existence, and
# the bootstrap step in zsh/steps.sh.

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
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

@test ".env.local template has ENV auto-generation markers" {
  grep -q '# --- ENV-START ---' "$REPO_ROOT/zsh/.env.local.template"
  grep -q '# --- ENV-END ---' "$REPO_ROOT/zsh/.env.local.template"
}

@test ".env.local template env section is empty (populated at runtime)" {
  # The template ships with empty markers — content is generated directly into ~/.env.local
  local content
  content=$(awk '/# --- ENV-START ---/{s=1;next} /# --- ENV-END ---/{s=0} s' "$REPO_ROOT/zsh/.env.local.template")
  [ -z "$content" ]
}

# ── step_env_local ───────────────────────────────────────────────────────────

@test "zsh/steps.sh defines step_env_local function" {
  grep -q 'step_env_local' "$REPO_ROOT/zsh/steps.sh"
}

@test "step_env_local creates ~/.env.local from template when absent" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local" \
    >/dev/null 2>&1

  [ -f "$FAKE_HOME/.env.local" ]
  rm -rf "$TMPDIR"
}

@test "step_env_local regenerates marker section without touching user values" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_SCAN="$TMPDIR/scan"
  mkdir -p "$FAKE_HOME" "$FAKE_SCAN/test"

  # Create a minimal registry with one env var
  cat > "$FAKE_SCAN/test/test.env.yml" <<'EOF'
meta:
  section: "Test Tools"
  validation: none
  install_check: false
env:
  - var: TEST_NEW_VAR
    comment: a test variable
EOF

  # Create existing .env.local with old marker content and user values below
  cat > "$FAKE_HOME/.env.local" <<'EOF'
# header
# --- ENV-START ---
# old var
# export OLD_VAR=
# --- ENV-END ---
export MY_SECRET=keep-this
EOF

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" REGISTRY_SCAN_DIR="$FAKE_SCAN" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local" \
    >/dev/null 2>&1

  # New registry content is present
  grep -q 'TEST_NEW_VAR' "$FAKE_HOME/.env.local"
  # Old content is gone
  run grep 'OLD_VAR' "$FAKE_HOME/.env.local"
  [ "$status" -ne 0 ]
  # User values preserved
  grep -q 'MY_SECRET=keep-this' "$FAKE_HOME/.env.local"
  # Header preserved
  grep -q '# header' "$FAKE_HOME/.env.local"
  rm -rf "$TMPDIR"
}

@test "step_env_local leaves file alone when no markers present" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  echo "export LEGACY_VAR=unchanged" > "$FAKE_HOME/.env.local"
  local before
  before=$(cat "$FAKE_HOME/.env.local")

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local" \
    >/dev/null 2>&1

  local after
  after=$(cat "$FAKE_HOME/.env.local")
  [ "$before" = "$after" ]
  rm -rf "$TMPDIR"
}

@test "migration moves uncommented exports below ENV-END markers" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  cat > "$FAKE_HOME/.env.local" <<'EOF'
# header
# --- ENV-START ---
# a comment
# export COMMENTED_VAR=
export REAL_TOKEN=my-secret
export ANOTHER=value
# --- ENV-END ---
# existing below
EOF

  HOME="$FAKE_HOME" \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/migrations/20260428-env-local-split.sh'; migration_20260428_env_local_split"

  # Uncommented exports should no longer be between markers
  local env_section
  env_section=$(sed -n '/# --- ENV-START ---/,/# --- ENV-END ---/p' "$FAKE_HOME/.env.local")
  run grep -c '^export' <<< "$env_section"
  [ "$output" = "0" ]

  # They should appear below ENV-END
  grep -q 'export REAL_TOKEN=my-secret' "$FAKE_HOME/.env.local"
  grep -q 'export ANOTHER=value' "$FAKE_HOME/.env.local"

  # Existing content below markers is preserved
  grep -q '# existing below' "$FAKE_HOME/.env.local"

  rm -rf "$TMPDIR"
}

@test "migration is a no-op when no uncommented exports inside markers" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  cat > "$FAKE_HOME/.env.local" <<'EOF'
# --- ENV-START ---
# export COMMENTED_VAR=
# --- ENV-END ---
export BELOW=fine
EOF

  local before
  before=$(cat "$FAKE_HOME/.env.local")

  HOME="$FAKE_HOME" \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/migrations/20260428-env-local-split.sh'; migration_20260428_env_local_split"

  local after
  after=$(cat "$FAKE_HOME/.env.local")
  [ "$before" = "$after" ]

  rm -rf "$TMPDIR"
}

@test "migration skips when ~/.env.local has no markers" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  echo "export LEGACY=unchanged" > "$FAKE_HOME/.env.local"

  local before
  before=$(cat "$FAKE_HOME/.env.local")

  HOME="$FAKE_HOME" \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/migrations/20260428-env-local-split.sh'; migration_20260428_env_local_split"

  local after
  after=$(cat "$FAKE_HOME/.env.local")
  [ "$before" = "$after" ]

  rm -rf "$TMPDIR"
}
