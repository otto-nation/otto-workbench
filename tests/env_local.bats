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

@test "step_env_local hoists a value set inside the markers and names it" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_SCAN="$TMPDIR/scan"
  mkdir -p "$FAKE_HOME" "$FAKE_SCAN/test"

  cat > "$FAKE_SCAN/test/test.env.yml" <<'EOF'
meta:
  section: "Test Tools"
  validation: none
env:
  - var: ANTHROPIC_API_KEY
    comment: Anthropic key
  - var: WITH_DEFAULT
    comment: has a default
    default: "on"
EOF

  # The user uncommented the catalogue line in place and pasted a key
  cat > "$FAKE_HOME/.env.local" <<'EOF'
# header
# --- ENV-START ---
# Anthropic key
export ANTHROPIC_API_KEY=sk-ant-secret
# has a default
export WITH_DEFAULT=on
# --- ENV-END ---
# ─── Your values ───
EOF

  run env HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" REGISTRY_SCAN_DIR="$FAKE_SCAN" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local"
  [ "$status" -eq 0 ]

  # The value survives, below ENV-END
  grep -q 'export ANTHROPIC_API_KEY=sk-ant-secret' "$FAKE_HOME/.env.local"
  local below
  below=$(awk '/# --- ENV-END ---/{s=1;next} s' "$FAKE_HOME/.env.local")
  grep -q 'export ANTHROPIC_API_KEY=sk-ant-secret' <<< "$below"

  # The generated section no longer carries it
  local inside
  inside=$(awk '/# --- ENV-START ---/{s=1;next} /# --- ENV-END ---/{s=0} s' "$FAKE_HOME/.env.local")
  run grep 'sk-ant-secret' <<< "$inside"
  [ "$status" -ne 0 ]

  # A generated default stays inside the markers — it is not a user value
  inside=$(awk '/# --- ENV-START ---/{s=1;next} /# --- ENV-END ---/{s=0} s' "$FAKE_HOME/.env.local")
  grep -q 'export WITH_DEFAULT=on' <<< "$inside"

  rm -rf "$TMPDIR"
}

@test "step_env_local warns naming each relocated variable" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_SCAN="$TMPDIR/scan"
  mkdir -p "$FAKE_HOME" "$FAKE_SCAN/test"

  cat > "$FAKE_SCAN/test/test.env.yml" <<'EOF'
meta:
  section: "Test Tools"
  validation: none
env:
  - var: TEST_VAR
    comment: a test variable
EOF

  cat > "$FAKE_HOME/.env.local" <<'EOF'
# --- ENV-START ---
export ANTHROPIC_API_KEY=sk-ant-secret
PLAIN_ASSIGN=no-export
# --- ENV-END ---
EOF

  run env HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" REGISTRY_SCAN_DIR="$FAKE_SCAN" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local"
  [ "$status" -eq 0 ]
  [[ "$output" == *"ANTHROPIC_API_KEY"* ]]
  [[ "$output" == *"PLAIN_ASSIGN"* ]]
  [[ "$output" != *"sk-ant-secret"* ]]

  grep -q 'PLAIN_ASSIGN=no-export' "$FAKE_HOME/.env.local"
  rm -rf "$TMPDIR"
}

@test "step_env_local hoisted value wins over a stale copy below ENV-END" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_SCAN="$TMPDIR/scan"
  mkdir -p "$FAKE_HOME" "$FAKE_SCAN/test"

  cat > "$FAKE_SCAN/test/test.env.yml" <<'EOF'
meta:
  section: "Test Tools"
  validation: none
env:
  - var: ANTHROPIC_API_KEY
    comment: Anthropic key
EOF

  # The same var is set in both places: freshly pasted inside the markers, and
  # left over below ENV-END from an earlier sync.
  cat > "$FAKE_HOME/.env.local" <<'EOF'
# header
# --- ENV-START ---
# Anthropic key
export ANTHROPIC_API_KEY=new-secret
# --- ENV-END ---
# ─── Your values ───
export ANTHROPIC_API_KEY=old-secret
EOF

  run env HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" REGISTRY_SCAN_DIR="$FAKE_SCAN" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local"
  [ "$status" -eq 0 ]

  # Both assignments are present, and the rescued one comes last
  local new_line old_line
  new_line=$(grep -n 'ANTHROPIC_API_KEY=new-secret' "$FAKE_HOME/.env.local" | cut -d: -f1)
  old_line=$(grep -n 'ANTHROPIC_API_KEY=old-secret' "$FAKE_HOME/.env.local" | cut -d: -f1)
  [ -n "$new_line" ]
  [ -n "$old_line" ]
  [ "$new_line" -gt "$old_line" ]

  # Sourcing the file therefore yields the rescued value, not the stale one
  run env -i bash -c ". '$FAKE_HOME/.env.local'; printf '%s' \"\$ANTHROPIC_API_KEY\""
  [ "$output" = "new-secret" ]

  rm -rf "$TMPDIR"
}

@test "step_env_local hoists a generated default the user annotated" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_SCAN="$TMPDIR/scan"
  mkdir -p "$FAKE_HOME" "$FAKE_SCAN/test"

  cat > "$FAKE_SCAN/test/test.env.yml" <<'EOF'
meta:
  section: "Test Tools"
  validation: none
env:
  - var: WITH_DEFAULT
    comment: has a default
    default: "on"
EOF

  # Matching is exact string equality by design: the generated default carries
  # an inline comment the generator did not write, so it counts as a stray.
  cat > "$FAKE_HOME/.env.local" <<'EOF'
# --- ENV-START ---
# has a default
export WITH_DEFAULT=on # keep this on
# --- ENV-END ---
EOF

  run env HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" REGISTRY_SCAN_DIR="$FAKE_SCAN" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local"
  [ "$status" -eq 0 ]
  [[ "$output" == *"WITH_DEFAULT"* ]]

  # The annotated line survives below ENV-END; the pristine default is restored
  # inside the markers.
  local below inside
  below=$(awk '/# --- ENV-END ---/{s=1;next} s' "$FAKE_HOME/.env.local")
  grep -q 'export WITH_DEFAULT=on # keep this on' <<< "$below"
  inside=$(awk '/# --- ENV-START ---/{s=1;next} /# --- ENV-END ---/{s=0} s' "$FAKE_HOME/.env.local")
  grep -q '^export WITH_DEFAULT=on$' <<< "$inside"

  rm -rf "$TMPDIR"
}

@test "step_env_local accumulates repeated hoists under one header" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_SCAN="$TMPDIR/scan"
  mkdir -p "$FAKE_HOME" "$FAKE_SCAN/test"

  cat > "$FAKE_SCAN/test/test.env.yml" <<'EOF'
meta:
  section: "Test Tools"
  validation: none
env:
  - var: VAR_A
    comment: first
  - var: VAR_B
    comment: second
EOF

  cat > "$FAKE_HOME/.env.local" <<'EOF'
# --- ENV-START ---
export VAR_A=aaa
# --- ENV-END ---
EOF

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" REGISTRY_SCAN_DIR="$FAKE_SCAN" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local" >/dev/null 2>&1

  # A second hoist event: the user uncomments VAR_B inside the markers
  sed 's/^# export VAR_B=$/export VAR_B=bbb/' "$FAKE_HOME/.env.local" > "$TMPDIR/edited"
  mv "$TMPDIR/edited" "$FAKE_HOME/.env.local"
  grep -q '^export VAR_B=bbb$' "$FAKE_HOME/.env.local"

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" REGISTRY_SCAN_DIR="$FAKE_SCAN" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local" >/dev/null 2>&1

  # One header, not one per hoist event
  run grep -c 'Moved here by otto-workbench sync' "$FAKE_HOME/.env.local"
  [ "$output" = "1" ]

  # Oldest hoist first — new ones append after, never above
  local a_line b_line
  a_line=$(grep -n '^export VAR_A=aaa$' "$FAKE_HOME/.env.local" | cut -d: -f1)
  b_line=$(grep -n '^export VAR_B=bbb$' "$FAKE_HOME/.env.local" | cut -d: -f1)
  [ "$b_line" -gt "$a_line" ]

  rm -rf "$TMPDIR"
}

@test "step_env_local hoist is idempotent across repeated syncs" {
  load test_helper
  TMPDIR="$(mktemp -d)"
  FAKE_HOME="$TMPDIR/home"
  FAKE_SCAN="$TMPDIR/scan"
  mkdir -p "$FAKE_HOME" "$FAKE_SCAN/test"

  cat > "$FAKE_SCAN/test/test.env.yml" <<'EOF'
meta:
  section: "Test Tools"
  validation: none
env:
  - var: ANTHROPIC_API_KEY
    comment: Anthropic key
  - var: WITH_DEFAULT
    comment: has a default
    default: "on"
EOF

  cat > "$FAKE_HOME/.env.local" <<'EOF'
# --- ENV-START ---
export ANTHROPIC_API_KEY=sk-ant-secret
# --- ENV-END ---
EOF

  HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" REGISTRY_SCAN_DIR="$FAKE_SCAN" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local" >/dev/null 2>&1
  local first
  first=$(cat "$FAKE_HOME/.env.local")

  run env HOME="$FAKE_HOME" WORKBENCH_DIR="$REPO_ROOT" REGISTRY_SCAN_DIR="$FAKE_SCAN" NO_COLOR=1 \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/zsh/steps.sh'; step_env_local"
  [ "$status" -eq 0 ]

  # Second run finds nothing to hoist and leaves the file byte-identical
  [ "$first" = "$(cat "$FAKE_HOME/.env.local")" ]
  [[ "$output" != *"ANTHROPIC_API_KEY"* ]]

  run grep -c 'sk-ant-secret' "$FAKE_HOME/.env.local"
  [ "$output" = "1" ]
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

  run env HOME="$FAKE_HOME" \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/lib/migrations.sh'; . '$REPO_ROOT/zsh/migrations/20260428-env-local-split.sh'; migration_20260428_env_local_split"
  [ "$status" -eq 0 ]

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

  run env HOME="$FAKE_HOME" \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/lib/migrations.sh'; . '$REPO_ROOT/zsh/migrations/20260428-env-local-split.sh'; migration_20260428_env_local_split"
  # MIGRATION_NOOP — the file is here and holds nothing to move
  [ "$status" -eq 3 ]

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

  run env HOME="$FAKE_HOME" \
    bash -c ". '$REPO_ROOT/lib/ui.sh'; . '$REPO_ROOT/lib/migrations.sh'; . '$REPO_ROOT/zsh/migrations/20260428-env-local-split.sh'; migration_20260428_env_local_split"
  # MIGRATION_NOOP — a file predating the markers has no marker section to split
  [ "$status" -eq 3 ]

  local after
  after=$(cat "$FAKE_HOME/.env.local")
  [ "$before" = "$after" ]

  rm -rf "$TMPDIR"
}
