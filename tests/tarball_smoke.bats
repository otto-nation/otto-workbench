#!/usr/bin/env bats
# Smoke tests for the otto-ai-tools tarball — verifies the build script produces
# a valid, self-contained distribution with patched paths and importable modules.

bats_require_minimum_version 1.5.0

TEST_VERSION="0.99.0-test"

setup_file() {
  load 'test_helper'
  common_setup
  # Build the tarball once for all tests
  TARBALL_DIR="$BATS_FILE_TMPDIR/tarball_build"
  mkdir -p "$TARBALL_DIR"
  run bash -c "cd '$TARBALL_DIR' && '$REPO_ROOT/ai/bin/build-otto-ai-tools-tarball' '$TEST_VERSION'"
  if [[ "$status" -ne 0 ]]; then
    echo "Tarball build failed: $output" >&2
    return 1
  fi

  # Extract into a known location
  EXTRACT_DIR="$BATS_FILE_TMPDIR/extracted"
  mkdir -p "$EXTRACT_DIR"
  tar -xzf "$TARBALL_DIR/otto-ai-tools-${TEST_VERSION}.tar.gz" -C "$EXTRACT_DIR"
  TARBALL_ROOT="$EXTRACT_DIR/otto-ai-tools-${TEST_VERSION}"
  export TARBALL_DIR EXTRACT_DIR TARBALL_ROOT TEST_VERSION
}

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# ── 1. Tarball structure ────────────────────────────────────────────────────

@test "tarball contains bin/, lib/, agents/, VERSION" {
  [ -d "$TARBALL_ROOT/bin" ]
  [ -d "$TARBALL_ROOT/lib" ]
  [ -d "$TARBALL_ROOT/agents" ]
  [ -f "$TARBALL_ROOT/VERSION" ]
}

@test "tarball carries no __pycache__ or .pyc — the artifact hash must not depend on the build host" {
  local count
  count=$(find "$TARBALL_ROOT/lib" -name '__pycache__' -o -name '*.pyc' | wc -l | tr -d ' ')
  [ "$count" -eq 0 ]
}

# ── 2. VERSION file ────────────────────────────────────────────────────────

@test "VERSION file contains the build version" {
  run cat "$TARBALL_ROOT/VERSION"
  [ "$status" -eq 0 ]
  [ "$output" = "$TEST_VERSION" ]
}

# ── 3. review is valid Python ────────────────────────────────────────

@test "review parses without Python syntax errors" {
  run python3 -c "import py_compile; py_compile.compile('$TARBALL_ROOT/bin/review', doraise=True)"
  [ "$status" -eq 0 ]
}

# ── 3b. The lib-dir pin works in the tarball layout ─────────────────────────

@test "_libdir.py ships, and a bad pin is refused from the tarball too" {
  # `_libdir.py` is mode 644, so the build's executables-only loop does not
  # reach it and it is copied by name. Without it every script in here dies on
  # an ImportError the moment a pin is set, instead of on the refusal.
  [ -f "$TARBALL_ROOT/bin/_libdir.py" ]

  WORKBENCH_AI_LIB_DIR=/nonexistent run "$TARBALL_ROOT/bin/review" --help
  [ "$status" -eq 2 ]
  [[ "$output" == *"WORKBENCH_AI_LIB_DIR"* ]]
  [[ "$output" != *"Traceback"* ]]
}

# ── 4. review uses Python shebang ─────────────────────────────────────

@test "review has Python shebang" {
  run head -1 "$TARBALL_ROOT/bin/review"
  [ "$status" -eq 0 ]
  [[ "$output" == *"python3"* ]]
}

# ── 5. --help ───────────────────────────────────────────────────────────────

@test "review --help exits 0" {
  run "$TARBALL_ROOT/bin/review" --help
  [ "$status" -eq 0 ]
}

# ── 6. --version ────────────────────────────────────────────────────────────

@test "review --version exits 0" {
  run "$TARBALL_ROOT/bin/review" --version
  [ "$status" -eq 0 ]
  [[ "$output" == *"review"* ]]
}

@test "review version reads VERSION file in tarball context" {
  run "$TARBALL_ROOT/bin/review" --version
  [ "$status" -eq 0 ]
  [[ "$output" == *"$TEST_VERSION"* ]]
}

# passes-at-base: guards the shim's import graph in the tarball layout; it ran before the move too
@test "otto-log and ai-usage-log run from the tarball layout" {
  run "$TARBALL_ROOT/bin/ai-usage-log" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"usage ledger"* ]]
  run "$TARBALL_ROOT/bin/otto-log" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Query trail files"* ]]
}
@test "retro-consume runs from the tarball layout" {
  run "$TARBALL_ROOT/bin/retro-consume" --version
  [ "$status" -eq 0 ]
  [[ "$output" == *"retro-consume"* ]]
}

@test "retro-scan runs from the tarball layout" {
  run "$TARBALL_ROOT/bin/retro-scan" --version
  [ "$status" -eq 0 ]
  [[ "$output" == *"retro-scan"* ]]
}

@test "promote-scan runs from the tarball layout" {
  run "$TARBALL_ROOT/bin/promote-scan" --version
  [ "$status" -eq 0 ]
  [[ "$output" == *"promote-scan"* ]]
}

@test "dream-scan runs from the tarball layout" {
  run "$TARBALL_ROOT/bin/dream-scan" --version
  [ "$status" -eq 0 ]
  [[ "$output" == *"dream-scan"* ]]
}

@test "promote-scan refuses without a workbench in the tarball layout" {
  run env -u OTTO_WORKBENCH "$TARBALL_ROOT/bin/promote-scan"
  [ "$status" -eq 2 ]
  [[ "$output" == *"--workbench"* ]]
  [[ "$output" != *"Traceback"* ]]
}

# ── 7. review-orchestrate Python imports ────────────────────────────────────

@test "review-orchestrate Python imports succeed from tarball layout" {
  run python3 -c "
import sys
sys.path.insert(0, '$TARBALL_ROOT/lib')
from review import paths
from review import collect
from gh import pr_data
from gh import pr_pages
from gh import pr_reads
from review import prompt
from agent import session
from review import pipeline
from review import phases
from review import retry
from review import verify
from review import state
from review import fix
from review import types
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

# ── 8. review-post Python imports ───────────────────────────────────────────

@test "review-post Python imports succeed from tarball layout" {
  run python3 -c "
import sys
sys.path.insert(0, '$TARBALL_ROOT/lib')
from review import paths
from review import dedup
from review import format
from gh import pr_data
from gh import pr_pages
from gh import pr_reads
from review import posting
from review import types
print('ok')
"
  [ "$status" -eq 0 ]
  [ "$output" = "ok" ]
}

# ── 9. Standalone ui.sh facade ──────────────────────────────────────────────

@test "standalone ui.sh facade sources successfully with info available" {
  run bash -c "source '$TARBALL_ROOT/lib/ui.sh' && type -t info"
  [ "$status" -eq 0 ]
  [ "$output" = "function" ]
}

# ── 10. Review templates ───────────────────────────────────────────────────

@test "tarball includes all review templates (at least 4 .md files)" {
  local count
  count=$(find "$TARBALL_ROOT/lib/review-templates" -name '*.md' -type f | wc -l | tr -d ' ')
  [ "$count" -ge 4 ]
}

# ── 11. Reviewer agent ─────────────────────────────────────────────────────

@test "tarball includes reviewer agent" {
  [ -f "$TARBALL_ROOT/agents/reviewer.md" ]
}

# ── 12. Argument validation ────────────────────────────────────────────────

@test "unknown flag is rejected without building a tarball" {
  local probe_dir="$BATS_TEST_TMPDIR/probe"
  mkdir -p "$probe_dir"
  run bash -c "cd '$probe_dir' && '$REPO_ROOT/ai/bin/build-otto-ai-tools-tarball' --tool-schema"
  [ "$status" -eq 2 ]
  [[ "$output" == *"unknown option"* ]]
  [ -z "$(find "$probe_dir" -name '*.tar.gz')" ]
}

@test "extra positional arguments are rejected" {
  run "$REPO_ROOT/ai/bin/build-otto-ai-tools-tarball" 1.0.0 extra
  [ "$status" -eq 2 ]
  [[ "$output" == *"at most one argument"* ]]
}
