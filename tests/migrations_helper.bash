#!/usr/bin/env bash
# The fake workbench and runners shared by the migrations_* suites.

# _migrations_fake_workbench — write a fake workbench under $TMPDIR with ui.sh
# stubs and constants pointing at sandbox state. The caller copies the real
# libraries in afterwards, so each suite names the files it exercises.
_migrations_fake_workbench() {
  # Build a minimal fake workbench with ui.sh stubs and constants
  FAKE_ROOT="$TMPDIR/workbench"
  FAKE_STATE="$TMPDIR/state"
  FAKE_CONFIG="$TMPDIR/config"
  # Never the real ~/.config/workbench: run_all_migrations empties this path.
  FAKE_LEGACY="$TMPDIR/legacy"
  mkdir -p "$FAKE_ROOT/lib" "$FAKE_STATE"

  cat > "$FAKE_ROOT/lib/ui.sh" <<'STUB'
#!/usr/bin/env bash
WORKBENCH_DIR="${WORKBENCH_DIR}"
BOLD='' GREEN='' BLUE='' YELLOW='' RED='' CYAN='' DIM='' NC=''
info()    { echo "→ $*"; }
success() { echo "✓ $*"; }
warn()    { echo "⚠ $*"; }
err()     { echo "✗ $*" >&2; }
apply_config_patch() { :; }
STUB
  # Inject the actual WORKBENCH_DIR into the stub
  sed -i.bak "s|WORKBENCH_DIR=\"\${WORKBENCH_DIR}\"|WORKBENCH_DIR=\"$FAKE_ROOT\"|" "$FAKE_ROOT/lib/ui.sh" && rm -f "$FAKE_ROOT/lib/ui.sh.bak"

  cat > "$FAKE_ROOT/lib/constants.sh" <<CONST
#!/usr/bin/env bash
WORKBENCH_DIR="$FAKE_ROOT"
LIB_SRC_DIR="$FAKE_ROOT/lib"
WORKBENCH_STATE_DIR="$FAKE_STATE"
WORKBENCH_CONFIG_DIR="$FAKE_CONFIG"
LEGACY_WORKBENCH_ROOT="$FAKE_LEGACY"
MIGRATIONS_STATE_FILE="$FAKE_STATE/migrations.applied"
PROJECTS_REGISTRY_FILE="$FAKE_STATE/projects.registry"
# No such file, so the backfill run_all_migrations does has no candidates.
CLAUDE_CONFIG_FILE="$TMPDIR/absent-claude.json"
. "$FAKE_ROOT/lib/portable.sh"
# The real projects.sh, not a stub: run_all_migrations calls into it, and it
# needs the constants above, so it loads from here rather than from ui.sh.
. "$FAKE_ROOT/lib/projects.sh"
CONST
}

# Helper: create a valid migration file in the fake workbench
create_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
${fn_name}() {
  :
}
EOF
}

# Helper: source the framework and run all migrations.
# Under `set -e`, matching the real caller (bin/otto-workbench). The marker
# printed afterwards is what proves the run returned rather than taking its
# caller down with it — a migration file's own `set -e` reaches this
# subshell through the source, so the abort is not hypothetical here.
run_migrations_in_fake() {
  (
    set -e
    . "$FAKE_ROOT/lib/ui.sh"
    . "$FAKE_ROOT/lib/constants.sh"
    . "$FAKE_ROOT/lib/migrations.sh"
    run_all_migrations
    echo "SYNC CONTINUED"
  )
}

# Helper: source the framework and adopt the legacy root only
adopt_in_fake() {
  (
    . "$FAKE_ROOT/lib/ui.sh"
    . "$FAKE_ROOT/lib/constants.sh"
    . "$FAKE_ROOT/lib/migrations.sh"
    adopt_legacy_workbench_root
  )
}

# Helper: create a migration that declares itself checkout-scoped and appends the
# repo path it was handed to $TMPDIR/exec.log.
create_checkout_migration() {
  local component="$1" filename="$2" fn_name="$3"
  mkdir -p "$FAKE_ROOT/$component/migrations"
  cat > "$FAKE_ROOT/$component/migrations/$filename" <<EOF
#!/usr/bin/env bash
# checkout-scoped: edits files inside each repo.
${fn_name}() {
  echo "\$1" >> "$TMPDIR/exec.log"
}
EOF
}

# Helper: put a repo in the registry.
#
# Written straight into the file rather than through project_register, which
# refuses anything under \$TMPDIR — and a temp directory is the only place a
# test may build a repo. project_registered, which is what the framework reads,
# applies no such rule.
register_fake_project() {
  mkdir -p "$1"
  printf '%s\n' "$1" >> "$FAKE_STATE/projects.registry"
}

# Helper: assert the state file holds KEY's checkout-scoped entry for a work
# tree. The separator the framework writes lives here rather than in every
# assertion that reads one back.
assert_checkout_entry() {
  local key="$1" repo="$2"
  grep -qxF "$key"$'\t'"$repo" "$FAKE_STATE/migrations.applied"
}
