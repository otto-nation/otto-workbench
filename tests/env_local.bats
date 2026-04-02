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
  # Must use [[ -f ... ]] && source pattern — not a bare source that errors if absent
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

@test ".env.local template env section is populated after generation" {
  # The generated section should contain at least one export line
  local content
  content=$(sed -n '/# --- ENV-START ---/,/# --- ENV-END ---/p' "$REPO_ROOT/zsh/.env.local.template")
  echo "$content" | grep -q 'export'
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

@test "_env_local_bootstrap adds new vars into existing file with empty markers" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  # Create an existing .env.local with empty ENV markers and user content
  cat > "$FAKE_HOME/.env.local" <<'EOF'
# my header
# --- ENV-START ---
# --- ENV-END ---
export MY_CUSTOM_VAR=keep_me
EOF

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; _env_local_bootstrap" \
    >/dev/null 2>&1

  # User content preserved
  grep -q 'MY_CUSTOM_VAR=keep_me' "$FAKE_HOME/.env.local"
  # ENV section populated from template
  grep -q 'export' "$FAKE_HOME/.env.local"
  # Header preserved
  grep -q '# my header' "$FAKE_HOME/.env.local"
  rm -rf "$TMPDIR"
}

@test "_env_local_bootstrap preserves user-filled values" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  # Create a .env.local where the user has uncommented and filled in a value
  cat > "$FAKE_HOME/.env.local" <<'EOF'
# --- ENV-START ---
# user set this
export CONTEXT7_API_KEY=ctx7sk-my-real-key
# --- ENV-END ---
EOF

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; _env_local_bootstrap" \
    >/dev/null 2>&1

  # User's filled-in value is preserved — not overwritten by template default
  grep -q 'CONTEXT7_API_KEY=ctx7sk-my-real-key' "$FAKE_HOME/.env.local"
  # Template default does NOT appear
  run grep 'CONTEXT7_API_KEY=ctx7sk-$' "$FAKE_HOME/.env.local"
  [ "$status" -ne 0 ]
  rm -rf "$TMPDIR"
}

@test "_env_local_bootstrap does not duplicate existing commented vars" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  # Create a .env.local that already has the template defaults (commented)
  cp "$REPO_ROOT/zsh/.env.local.template" "$FAKE_HOME/.env.local"

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; _env_local_bootstrap" \
    >/dev/null 2>&1

  # No vars should be duplicated — count occurrences of CONTEXT7_API_KEY
  local count
  count=$(grep -c 'CONTEXT7_API_KEY' "$FAKE_HOME/.env.local")
  [ "$count" -eq 1 ]
  rm -rf "$TMPDIR"
}

@test "_env_local_bootstrap preserves content without ENV markers" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  mkdir -p "$FAKE_HOME"

  echo "export LEGACY_VAR=unchanged" > "$FAKE_HOME/.env.local"

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; _env_local_bootstrap" \
    >/dev/null 2>&1

  grep -q 'LEGACY_VAR=unchanged' "$FAKE_HOME/.env.local"
  rm -rf "$TMPDIR"
}
