#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
}

setup() {
  load 'test_helper'
  common_setup
  source "$REPO_ROOT/bin/local/generate-lib-reference"
  TMPDIR="$(mktemp -d)"

  # LIB_DIR is the env hook the script resolves through, so the fixtures below
  # stand in for the real lib/ and no test reads the modules it documents.
  export LIB_DIR="$TMPDIR/lib"
  mkdir -p "$LIB_DIR/ai"
  printf '#!/usr/bin/env bash\n' > "$LIB_DIR/ui.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
  unset LIB_DIR
}

# _facade MODULE... — rewrites the fixture ui.sh to source the named modules,
# in the form the real facade uses.
_facade() {
  local name
  printf '#!/usr/bin/env bash\n' > "$LIB_DIR/ui.sh"
  for name in "$@"; do
    printf '. "$_ui_lib_dir/%s"\n' "$name" >> "$LIB_DIR/ui.sh"
  done
}

# ── Doc rows ─────────────────────────────────────────────────────────────────

@test "doc rows split signature from purpose on the em dash" {
  cat > "$LIB_DIR/output.sh" << 'EOF'
# info MESSAGE — blue info message with an arrow.
info() { echo "$*"; }
EOF
  run _lib_doc_rows "$LIB_DIR/output.sh"
  [ "$output" = "$(printf 'info MESSAGE\tblue info message with an arrow.')" ]
}

@test "doc rows take the purpose from the description when the signature stands alone" {
  cat > "$LIB_DIR/menu.sh" << 'EOF'
# select_menu RESULT_VAR COUNT
#
# Displays a numbered selection prompt.
select_menu() { :; }
EOF
  run _lib_doc_rows "$LIB_DIR/menu.sh"
  [ "$output" = "$(printf 'select_menu RESULT_VAR COUNT\tDisplays a numbered selection prompt.')" ]
}

@test "doc rows join a wrapped purpose and stop at the end of the sentence" {
  cat > "$LIB_DIR/portable.sh" << 'EOF'
# file_birth PATH — birth time in epoch seconds. Prints 0 on
# filesystems that do not record one. Callers must treat 0 as unknown.
file_birth() { :; }
EOF
  run _lib_doc_rows "$LIB_DIR/portable.sh"
  [ "$output" = "$(printf 'file_birth PATH\tbirth time in epoch seconds. Prints 0 on filesystems that do not record one.')" ]
}

@test "doc rows pair a comment with its function across intervening declarations" {
  cat > "$LIB_DIR/install.sh" << 'EOF'
# resolve_known_components ARRAY_REF — resolves component names to paths.
declare -a KNOWN_COMPONENTS=()
declare -A COMPONENT_PATHS=()
resolve_known_components() { :; }
EOF
  run _lib_doc_rows "$LIB_DIR/install.sh"
  [ "$output" = "$(printf 'resolve_known_components ARRAY_REF\tresolves component names to paths.')" ]
}

@test "functions table skips private functions and escapes pipes" {
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

@test "functions table is empty for a module with no public functions" {
  cat > "$LIB_DIR/constants.sh" << 'EOF'
# Shared constants.
WORKBENCH_NAME="workbench"
EOF
  run _lib_functions_table "$LIB_DIR/constants.sh"
  [ -z "$output" ]
}

# ── Header extraction ────────────────────────────────────────────────────────

@test "header prints the comment block under the shebang, uncommented" {
  cat > "$LIB_DIR/output.sh" << 'EOF'
#!/usr/bin/env bash
# Terminal output helpers.
#
# Every message goes to stderr.
info() { :; }
EOF
  run _lib_header "$LIB_DIR/output.sh"
  [ "$output" = "$(printf 'Terminal output helpers.\n\nEvery message goes to stderr.')" ]
}

@test "header drops linter directives and the group marker" {
  cat > "$LIB_DIR/registries.sh" << 'EOF'
#!/usr/bin/env bash
# doc-group: registry
# shellcheck disable=SC2034
# Registry readers.
info() { :; }
EOF
  run _lib_header "$LIB_DIR/registries.sh"
  [ "$output" = "Registry readers." ]
}

@test "header stops at the first line of code" {
  cat > "$LIB_DIR/roots.sh" << 'EOF'
#!/usr/bin/env bash
# The three workbench roots.
WORKBENCH_STATE_DIR="/tmp/state"
# Not part of the header.
EOF
  run _lib_header "$LIB_DIR/roots.sh"
  [ "$output" = "The three workbench roots." ]
}

# ── Groups ───────────────────────────────────────────────────────────────────

@test "group defaults to core for lib and ai for lib/ai" {
  printf '#!/usr/bin/env bash\n# Output.\n' > "$LIB_DIR/output.sh"
  printf '#!/usr/bin/env bash\n# PR context.\n' > "$LIB_DIR/ai/pr.sh"
  run _lib_group "$LIB_DIR/output.sh"
  [ "$output" = "core" ]
  run _lib_group "$LIB_DIR/ai/pr.sh"
  [ "$output" = "ai" ]
}

@test "a declared group marker overrides the directory default" {
  printf '#!/usr/bin/env bash\n# doc-group: registry\n# Registries.\n' > "$LIB_DIR/registries.sh"
  run _lib_group "$LIB_DIR/registries.sh"
  [ "$output" = "registry" ]
}

@test "--groups lists every declared key once" {
  printf '#!/usr/bin/env bash\n# Output.\n' > "$LIB_DIR/output.sh"
  printf '#!/usr/bin/env bash\n# doc-group: registry\n# Registries.\n' > "$LIB_DIR/registries.sh"
  printf '#!/usr/bin/env bash\n# doc-group: registry\n# Conventions.\n' > "$LIB_DIR/conventions.sh"
  printf '#!/usr/bin/env bash\n# PR context.\n' > "$LIB_DIR/ai/pr.sh"
  run main --groups
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf 'ai\ncore\nregistry')" ]
}

@test "--group renders each module in the group alphabetically" {
  printf '#!/usr/bin/env bash\n# Output helpers.\n' > "$LIB_DIR/output.sh"
  printf '#!/usr/bin/env bash\n# Portability shims.\n' > "$LIB_DIR/portable.sh"
  printf '#!/usr/bin/env bash\n# PR context.\n' > "$LIB_DIR/ai/pr.sh"

  run main --group core
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '### output.sh\n\nOutput helpers.\n\n### portable.sh\n\nPortability shims.')" ]
}

@test "--group excludes the ui.sh facade, which has its own prose" {
  printf '#!/usr/bin/env bash\n# The facade.\n' > "$LIB_DIR/ui.sh"
  printf '#!/usr/bin/env bash\n# Output helpers.\n' > "$LIB_DIR/output.sh"
  run main --group core
  [ "$status" -eq 0 ]
  [[ "$output" != *"ui.sh"* ]]
}

@test "--group fails when no module declares the key" {
  printf '#!/usr/bin/env bash\n# Output helpers.\n' > "$LIB_DIR/output.sh"
  run main --group registry
  [ "$status" -ne 0 ]
  [[ "$output" == *"no module declares the group 'registry'"* ]]
}

@test "--group without a key fails instead of rendering an empty group" {
  run main --group
  [ "$status" -eq 2 ]
  [[ "$output" == *"--group requires a group key"* ]]
}

# ── Facade note ──────────────────────────────────────────────────────────────

@test "a module the facade sources carries the loaded-via note" {
  printf '#!/usr/bin/env bash\n# Output helpers.\n' > "$LIB_DIR/output.sh"
  printf '#!/usr/bin/env bash\n# Migration runner.\n' > "$LIB_DIR/migrations.sh"
  _facade "output.sh"

  run main --group core
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '### migrations.sh\n\nMigration runner.\n\n### output.sh\n\nOutput helpers.\n\nLoaded via `ui.sh`.')" ]
}

# ── Nested includes ──────────────────────────────────────────────────────────

@test "an include directive in a header is printed for compose-docs to expand" {
  cat > "$LIB_DIR/roots.sh" << 'EOF'
#!/usr/bin/env bash
# The three workbench roots.
#
# <!-- include: bin/local/generate-lib-reference --roots-table -->
EOF
  run main --group core
  [ "$status" -eq 0 ]
  [[ "$output" == *'<!-- include: bin/local/generate-lib-reference --roots-table -->'* ]]
}

# ── Roots table ──────────────────────────────────────────────────────────────

@test "roots table reads the constant, XDG rung and default off the assignment" {
  cat > "$LIB_DIR/roots.sh" << 'EOF'
# Hand-authored settings: config.yml, overrides/.
WORKBENCH_CONFIG_DIR="$(_wb_root "${WORKBENCH_CONFIG_DIR:-}" "${XDG_CONFIG_HOME:-}" "$HOME/.config/workbench")"
EOF
  run _lib_roots_table
  [[ "$output" == *'| `WORKBENCH_CONFIG_DIR` | Hand-authored settings: config.yml, overrides/ | `XDG_CONFIG_HOME` | `~/.config/workbench` |'* ]]
}

@test "roots table fails when lib/roots.sh is missing" {
  run _lib_roots_table
  [ "$status" -ne 0 ]
  [[ "$output" == *"lib/roots.sh is missing"* ]]
}

# ── Doc completeness ─────────────────────────────────────────────────────────

@test "rendering fails on a public function with no doc comment" {
  cat > "$LIB_DIR/output.sh" << 'EOF'
#!/usr/bin/env bash
# Output helpers.
# info MESSAGE — blue info message with an arrow.
info() { echo "$*"; }

warn() { echo "$*"; }
EOF
  run main --group core
  [ "$status" -ne 0 ]
  [[ "$output" == *"lib/output.sh: warn has no doc comment"* ]]
}

@test "rendering passes when every public function is documented" {
  cat > "$LIB_DIR/output.sh" << 'EOF'
#!/usr/bin/env bash
# Output helpers.

# info MESSAGE — blue info message with an arrow.
info() { echo "$*"; }

# _shout MESSAGE — internal, needs no row.
_shout() { echo "$*"; }
EOF
  run main --group core
  [ "$status" -eq 0 ]
  [[ "$output" == *'| `info MESSAGE` | blue info message with an arrow. |'* ]]
  [[ "$output" != *_shout* ]]
}

# ── CLI ──────────────────────────────────────────────────────────────────────

@test "no mode fails with usage" {
  run main
  [ "$status" -eq 2 ]
  [[ "$output" == *"Nothing to do"* ]]
}

@test "an unknown option fails instead of being read as a group key" {
  run main --modules
  [ "$status" -eq 2 ]
  [[ "$output" == *"Unknown option: --modules"* ]]
}

@test "--help lists every mode" {
  run main --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"--group"* ]]
  [[ "$output" == *"--roots-table"* ]]
  [[ "$output" == *"--groups"* ]]
}
