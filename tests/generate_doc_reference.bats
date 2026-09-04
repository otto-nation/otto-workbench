#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
}

setup() {
  load 'test_helper'
  common_setup
  source "$REPO_ROOT/bin/local/generate-doc-reference"
  TMPDIR="$(mktemp -d)"

  # SOURCE_ROOT is the env hook every source set resolves its globs against, so
  # the fixtures below stand in for the real trees and no test reads the modules
  # it documents.
  export SOURCE_ROOT="$TMPDIR"
  LIB_DIR="$SOURCE_ROOT/lib"
  AI_LIB_DIR="$SOURCE_ROOT/ai/lib"
  mkdir -p "$LIB_DIR/ai" "$AI_LIB_DIR"
  printf '#!/usr/bin/env bash\n' > "$LIB_DIR/ui.sh"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
  unset SOURCE_ROOT
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
  run _bash_doc_rows "$LIB_DIR/output.sh"
  [ "$output" = "$(printf 'info MESSAGE\tblue info message with an arrow.')" ]
}

@test "doc rows take the purpose from the description when the signature stands alone" {
  cat > "$LIB_DIR/menu.sh" << 'EOF'
# select_menu RESULT_VAR COUNT
#
# Displays a numbered selection prompt.
select_menu() { :; }
EOF
  run _bash_doc_rows "$LIB_DIR/menu.sh"
  [ "$output" = "$(printf 'select_menu RESULT_VAR COUNT\tDisplays a numbered selection prompt.')" ]
}

@test "doc rows join a wrapped purpose to the end of the paragraph" {
  cat > "$LIB_DIR/portable.sh" << 'EOF'
# file_birth PATH — birth time in epoch seconds. Prints 0 on
# filesystems that do not record one. Callers must treat 0 as unknown.
file_birth() { :; }
EOF
  run _bash_doc_rows "$LIB_DIR/portable.sh"
  [ "$output" = "$(printf 'file_birth PATH\tbirth time in epoch seconds. Prints 0 on filesystems that do not record one. Callers must treat 0 as unknown.')" ]
}

@test "doc rows keep the sentence describing a later argument" {
  cat > "$LIB_DIR/prompts.sh" << 'EOF'
# prompt_commit DIFF [RETRY_PREAMBLE] [SURFACE_NOTE] — the commit prompt.
# RETRY_PREAMBLE is prepended to it.
# SURFACE_NOTE is rendered after the rules.
prompt_commit() { :; }
EOF
  run _bash_doc_rows "$LIB_DIR/prompts.sh"
  [ "$output" = "$(printf 'prompt_commit DIFF [RETRY_PREAMBLE] [SURFACE_NOTE]\tthe commit prompt. RETRY_PREAMBLE is prepended to it. SURFACE_NOTE is rendered after the rules.')" ]
}

@test "doc rows stop the purpose at the rationale below the paragraph" {
  cat > "$LIB_DIR/git.sh" << 'EOF'
# resolve_default_branch — the remote's default branch.
#
# symbolic-ref, not rev-parse: rev-parse still prints a name when the symref
# is missing, which defeats the fallback.
resolve_default_branch() { :; }
EOF
  run _bash_doc_rows "$LIB_DIR/git.sh"
  [ "$output" = "$(printf "resolve_default_branch\tthe remote's default branch.")" ]
}

@test "doc rows drop the indentation of a continued line" {
  cat > "$LIB_DIR/components.sh" << 'EOF'
# discover_step_files ARRAY_REF — every steps.sh file:
#   WORKBENCH_DIR/*/steps.sh and WORKBENCH_DIR/*/*/steps.sh
discover_step_files() { :; }
EOF
  run _bash_doc_rows "$LIB_DIR/components.sh"
  [ "$output" = "$(printf 'discover_step_files ARRAY_REF\tevery steps.sh file: WORKBENCH_DIR/*/steps.sh and WORKBENCH_DIR/*/*/steps.sh')" ]
}

@test "doc rows pair a comment with its function across intervening declarations" {
  cat > "$LIB_DIR/install.sh" << 'EOF'
# resolve_known_components ARRAY_REF — resolves component names to paths.
declare -a KNOWN_COMPONENTS=()
declare -A COMPONENT_PATHS=()
resolve_known_components() { :; }
EOF
  run _bash_doc_rows "$LIB_DIR/install.sh"
  [ "$output" = "$(printf 'resolve_known_components ARRAY_REF\tresolves component names to paths.')" ]
}

@test "functions table skips private functions and escapes pipes" {
  cat > "$LIB_DIR/menu.sh" << 'EOF'
# select_menu RESULT_VAR [--default all|skip] — numbered selection prompt.
select_menu() { :; }

# _render_row INDEX — internal.
_render_row() { :; }
EOF
  run _functions_table lib "$LIB_DIR/menu.sh"
  [[ "$output" == *'| `select_menu RESULT_VAR [--default all\|skip]` | numbered selection prompt. |'* ]]
  [[ "$output" != *_render_row* ]]
}

@test "functions table is empty for a module with no public functions" {
  cat > "$LIB_DIR/constants.sh" << 'EOF'
# Shared constants.
WORKBENCH_NAME="workbench"
EOF
  run _functions_table lib "$LIB_DIR/constants.sh"
  [ -z "$output" ]
}

@test "a set that declares no symbol syntax renders no functions table" {
  cat > "$AI_LIB_DIR/proc.py" << 'EOF'
"""Subprocess helpers."""

# doc-group: platform

def run(cmd):
    """Run CMD."""
EOF
  run _functions_table ai-lib "$AI_LIB_DIR/proc.py"
  [ -z "$output" ]
}

# ── Doc block extraction ─────────────────────────────────────────────────────

@test "doc block prints the comment block under the shebang, uncommented" {
  cat > "$LIB_DIR/output.sh" << 'EOF'
#!/usr/bin/env bash
# Terminal output helpers.
#
# Every message goes to stderr.
info() { :; }
EOF
  run _doc_block lib "$LIB_DIR/output.sh"
  [ "$output" = "$(printf 'Terminal output helpers.\n\nEvery message goes to stderr.')" ]
}

@test "doc block drops linter directives and the group marker" {
  cat > "$LIB_DIR/registries.sh" << 'EOF'
#!/usr/bin/env bash
# doc-group: registry
# shellcheck disable=SC2034
# Registry readers.
info() { :; }
EOF
  run _doc_block lib "$LIB_DIR/registries.sh"
  [ "$output" = "Registry readers." ]
}

@test "doc block stops at the first line of code" {
  cat > "$LIB_DIR/roots.sh" << 'EOF'
#!/usr/bin/env bash
# The three workbench roots.
WORKBENCH_STATE_DIR="/tmp/state"
# Not part of the header.
EOF
  run _doc_block lib "$LIB_DIR/roots.sh"
  [ "$output" = "The three workbench roots." ]
}

@test "doc block reads a multi-line Python docstring without its quotes" {
  cat > "$AI_LIB_DIR/supersession.py" << 'EOF'
"""Whether a branch's reason to exist is already gone.

The skew is in the commit dates.
"""

# doc-group: pr-state

import re
EOF
  run _doc_block ai-lib "$AI_LIB_DIR/supersession.py"
  [ "$output" = "$(printf "Whether a branch's reason to exist is already gone.\n\nThe skew is in the commit dates.")" ]
}

@test "doc block reads a single-line Python docstring" {
  printf '"""Issue tracking integration."""\n\n# doc-group: publishing\n' > "$AI_LIB_DIR/review_issue.py"
  run _doc_block ai-lib "$AI_LIB_DIR/review_issue.py"
  [ "$output" = "Issue tracking integration." ]
}

@test "doc block skips a shebang above the Python docstring" {
  cat > "$AI_LIB_DIR/ci_failures.py" << 'EOF'
#!/usr/bin/env python3
"""CI failure lifecycle tracking."""

# doc-group: pr-state
EOF
  run _doc_block ai-lib "$AI_LIB_DIR/ci_failures.py"
  [ "$output" = "CI failure lifecycle tracking." ]
}

@test "doc block is empty for a Python module with no docstring" {
  printf 'from __future__ import annotations\n\n# doc-group: platform\n' > "$AI_LIB_DIR/serde.py"
  run _doc_block ai-lib "$AI_LIB_DIR/serde.py"
  [ -z "$output" ]
}

# ── Groups ───────────────────────────────────────────────────────────────────

@test "group defaults to core for lib and ai for lib/ai" {
  printf '#!/usr/bin/env bash\n# Output.\n' > "$LIB_DIR/output.sh"
  printf '#!/usr/bin/env bash\n# PR context.\n' > "$LIB_DIR/ai/pr.sh"
  run main --set lib --groups
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf 'ai\ncore')" ]
}

@test "a declared group marker overrides the directory default" {
  printf '#!/usr/bin/env bash\n# doc-group: registry\n# Registries.\n' > "$LIB_DIR/registries.sh"
  run _file_group lib "$LIB_DIR/registries.sh" core
  [ "$output" = "registry" ]
}

@test "a doc-group line below the header does not declare a group" {
  cat > "$LIB_DIR/output.sh" << 'EOF'
#!/usr/bin/env bash
# Output helpers.
info() { :; }

# The convention is a header line reading: # doc-group: registry
EOF
  run _file_group lib "$LIB_DIR/output.sh" core
  [ "$output" = "core" ]
}

@test "a Python module declares its group below the docstring" {
  cat > "$AI_LIB_DIR/trail.py" << 'EOF'
"""Structured trail logging.

Every script appends to one root.
"""

# doc-group: platform

from __future__ import annotations
EOF
  run _file_group ai-lib "$AI_LIB_DIR/trail.py" ""
  [ "$output" = "platform" ]
}

@test "a Python doc-group line below the imports does not declare a group" {
  cat > "$AI_LIB_DIR/trail.py" << 'EOF'
"""Structured trail logging."""

import re

# doc-group: platform
EOF
  run _file_group ai-lib "$AI_LIB_DIR/trail.py" ""
  [ "$status" -ne 0 ]
  [[ "$output" == *"ai/lib/trail.py declares no group"* ]]
}

@test "--groups lists every declared key once" {
  printf '#!/usr/bin/env bash\n# Output.\n' > "$LIB_DIR/output.sh"
  printf '#!/usr/bin/env bash\n# doc-group: registry\n# Registries.\n' > "$LIB_DIR/registries.sh"
  printf '#!/usr/bin/env bash\n# doc-group: registry\n# Conventions.\n' > "$LIB_DIR/conventions.sh"
  printf '#!/usr/bin/env bash\n# PR context.\n' > "$LIB_DIR/ai/pr.sh"
  run main --set lib --groups
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf 'ai\ncore\nregistry')" ]
}

@test "--group renders each module in the group in byte order" {
  printf '#!/usr/bin/env bash\n# Output helpers.\n' > "$LIB_DIR/output.sh"
  printf '#!/usr/bin/env bash\n# Portability shims.\n' > "$LIB_DIR/portable.sh"
  printf '#!/usr/bin/env bash\n# PR context.\n' > "$LIB_DIR/ai/pr.sh"

  run main --set lib --group core
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '### output.sh\n\nOutput helpers.\n\n### portable.sh\n\nPortability shims.')" ]
}

@test "module order does not follow the caller's collation" {
  # `.` sorts before `_` in byte order and after it under en_US.UTF-8, so a
  # contributor whose shell disagrees with CI's would otherwise regenerate a doc
  # differing only in module order and fail validate-docs-composed for nothing.
  # Run as a subprocess: the collation the script pins is pinned at startup, and
  # sourcing it into the test shell is not how compose-docs reaches it.
  # Not a pipe into grep: sourcing the generator brought its `set -o pipefail`
  # into this shell, and grep -q closing the pipe early would fail the guard.
  local locales
  locales="$(locale -a)"
  if [[ "$locales" != *"en_US.UTF-8"* ]]; then
    skip "en_US.UTF-8 not generated on this machine"
  fi
  printf '#!/usr/bin/env bash\n# The backend.\n' > "$LIB_DIR/backend.sh"
  printf '#!/usr/bin/env bash\n# The Pi backend.\n' > "$LIB_DIR/backend_pi.sh"

  run env LC_ALL=en_US.UTF-8 "$REPO_ROOT/bin/local/generate-doc-reference" --set lib --group core
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '### backend.sh\n\nThe backend.\n\n### backend_pi.sh\n\nThe Pi backend.')" ]
}

@test "--group excludes the ui.sh facade, which has its own prose" {
  printf '#!/usr/bin/env bash\n# The facade.\n' > "$LIB_DIR/ui.sh"
  printf '#!/usr/bin/env bash\n# Output helpers.\n' > "$LIB_DIR/output.sh"
  run main --set lib --group core
  [ "$status" -eq 0 ]
  [[ "$output" != *"ui.sh"* ]]
}

@test "--group fails when no module declares the key" {
  printf '#!/usr/bin/env bash\n# Output helpers.\n' > "$LIB_DIR/output.sh"
  run main --set lib --group registry
  [ "$status" -ne 0 ]
  [[ "$output" == *"no module in the 'lib' set declares the group 'registry'"* ]]
}

@test "--group without a key fails instead of rendering an empty group" {
  run main --set lib --group
  [ "$status" -eq 2 ]
  [[ "$output" == *"--group requires a group key"* ]]
}

# ── Source sets ──────────────────────────────────────────────────────────────

@test "--sets lists every source set" {
  run main --sets
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf 'ai-lib\nlib')" ]
}

@test "the ai-lib set renders module docstrings under their own heading" {
  mkdir -p "$AI_LIB_DIR/core" "$AI_LIB_DIR/pr"
  cat > "$AI_LIB_DIR/core/publishing.py" << 'EOF'
"""The gate every outward-facing write passes through.

Callers print what they would have sent.
"""

# doc-group: publishing
EOF
  printf '"""Reply threads."""\n\n# doc-group: publishing\n' > "$AI_LIB_DIR/pr/comments.py"
  run main --set ai-lib --group publishing
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '### core/publishing.py\n\nThe gate every outward-facing write passes through.\n\nCallers print what they would have sent.\n\n### pr/comments.py\n\nReply threads.')" ]
}

@test "the two sets do not see each other's modules" {
  mkdir -p "$AI_LIB_DIR/core"
  printf '#!/usr/bin/env bash\n# Output helpers.\n' > "$LIB_DIR/output.sh"
  printf '"""Subprocess helpers."""\n\n# doc-group: platform\n' > "$AI_LIB_DIR/core/proc.py"
  run main --set lib --group core
  [[ "$output" != *"proc.py"* ]]
  run main --set ai-lib --group platform
  [[ "$output" != *"output.sh"* ]]
}

@test "a package's __init__.py is excluded rather than required to declare a group" {
  mkdir -p "$AI_LIB_DIR/core"
  printf '"""Layer 1 — foundation primitives."""\n' > "$AI_LIB_DIR/core/__init__.py"
  printf '"""Subprocess helpers."""\n\n# doc-group: platform\n' > "$AI_LIB_DIR/core/proc.py"
  run main --set ai-lib --groups
  [ "$status" -eq 0 ]
  [ "$output" = "platform" ]
}

@test "an unknown set fails instead of rendering nothing" {
  run main --set widgets --group core
  [ "$status" -eq 2 ]
  [[ "$output" == *"Unknown source set: widgets"* ]]
}

@test "--groups without a set fails instead of guessing one" {
  run main --groups
  [ "$status" -eq 2 ]
  [[ "$output" == *"--groups requires --set"* ]]
}

@test "--set without a value fails" {
  run main --set
  [ "$status" -eq 2 ]
  [[ "$output" == *"--set requires a source set"* ]]
}

# ── Facade note ──────────────────────────────────────────────────────────────

@test "a module the facade sources carries the loaded-via note" {
  printf '#!/usr/bin/env bash\n# Output helpers.\n' > "$LIB_DIR/output.sh"
  printf '#!/usr/bin/env bash\n# Migration runner.\n' > "$LIB_DIR/migrations.sh"
  _facade "output.sh"

  run main --set lib --group core
  [ "$status" -eq 0 ]
  [ "$output" = "$(printf '### migrations.sh\n\nMigration runner.\n\n### output.sh\n\nOutput helpers.\n\nLoaded via `ui.sh`.')" ]
}

# ── Nested includes ──────────────────────────────────────────────────────────

@test "an include directive in a doc block is printed for compose-docs to expand" {
  cat > "$LIB_DIR/roots.sh" << 'EOF'
#!/usr/bin/env bash
# The three workbench roots.
#
# <!-- include: bin/local/generate-doc-reference --roots-table -->
EOF
  run main --set lib --group core
  [ "$status" -eq 0 ]
  [[ "$output" == *'<!-- include: bin/local/generate-doc-reference --roots-table -->'* ]]
}

# ── Roots table ──────────────────────────────────────────────────────────────

@test "roots table reads the constant, XDG rung and default off the assignment" {
  cat > "$LIB_DIR/roots.sh" << 'EOF'
# Hand-authored settings: config.yml, overrides/.
WORKBENCH_CONFIG_DIR="$(_wb_root "$_wb_had_config" "${_WB_DERIVED_CONFIG_DIR:-}" "${XDG_CONFIG_HOME:-}" "$HOME/.config/workbench")"
_wb_mark "$_wb_had_config" "${_WB_DERIVED_CONFIG_DIR:-}" _WB_DERIVED_CONFIG_DIR "$WORKBENCH_CONFIG_DIR"
EOF
  run _roots_table
  [[ "$output" == *'| `WORKBENCH_CONFIG_DIR` | Hand-authored settings: config.yml, overrides/ | `XDG_CONFIG_HOME` | `~/.config/workbench` |'* ]]
}

@test "roots table fails when lib/roots.sh is missing" {
  run _roots_table
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
  run main --set lib --group core
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
  run main --set lib --group core
  [ "$status" -eq 0 ]
  [[ "$output" == *'| `info MESSAGE` | blue info message with an arrow. |'* ]]
  [[ "$output" != *_shout* ]]
}

@test "rendering fails on a module whose doc block is empty" {
  printf '#!/usr/bin/env bash\nWORKBENCH_NAME="workbench"\n' > "$LIB_DIR/constants.sh"
  run main --set lib --group core
  [ "$status" -ne 0 ]
  [[ "$output" == *"lib/constants.sh has no doc block"* ]]
}

@test "a module in a set with no default group must declare one" {
  mkdir -p "$AI_LIB_DIR/core"
  printf '"""Serialization helpers."""\n' > "$AI_LIB_DIR/core/serde.py"
  run main --set ai-lib --groups
  [ "$status" -ne 0 ]
  [[ "$output" == *"ai/lib/core/serde.py declares no group"* ]]
}

@test "a module with no docstring cannot declare a group above it" {
  mkdir -p "$AI_LIB_DIR/core"
  printf '# doc-group: platform\n\nfrom __future__ import annotations\n' > "$AI_LIB_DIR/core/serde.py"
  run main --set ai-lib --groups
  [ "$status" -ne 0 ]
  [[ "$output" == *"ai/lib/core/serde.py declares no group"* ]]
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
  [[ "$output" == *"--set"* ]]
  [[ "$output" == *"--group"* ]]
  [[ "$output" == *"--groups"* ]]
  [[ "$output" == *"--sets"* ]]
  [[ "$output" == *"--roots-table"* ]]
}
