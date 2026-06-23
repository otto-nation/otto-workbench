#!/usr/bin/env bats
# Smoke tests for the otto-ai-tools tarball — verifies the build script produces
# a valid, self-contained distribution with patched paths and importable modules.

bats_require_minimum_version 1.5.0

TEST_VERSION="0.99.0-test"

setup_file() {
  load 'test_helper'
  unset GIT_DIR GIT_WORK_TREE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES 2>/dev/null || true
  # Build the tarball once for all tests
  TARBALL_DIR="$BATS_FILE_TMPDIR/tarball_build"
  mkdir -p "$TARBALL_DIR"
  run bash -c "cd '$TARBALL_DIR' && '$REPO_ROOT/ai/claude/bin/build-otto-ai-tools-tarball' '$TEST_VERSION'"
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

# ── 2. VERSION file ────────────────────────────────────────────────────────

@test "VERSION file contains the build version" {
  run cat "$TARBALL_ROOT/VERSION"
  [ "$status" -eq 0 ]
  [ "$output" = "$TEST_VERSION" ]
}

# ── 3. claude-review is valid Python ────────────────────────────────────────

@test "claude-review parses without Python syntax errors" {
  run python3 -c "import py_compile; py_compile.compile('$TARBALL_ROOT/bin/claude-review', doraise=True)"
  [ "$status" -eq 0 ]
}

# ── 4. claude-review uses Python shebang ─────────────────────────────────────

@test "claude-review has Python shebang" {
  run head -1 "$TARBALL_ROOT/bin/claude-review"
  [ "$status" -eq 0 ]
  [[ "$output" == *"python3"* ]]
}

# ── 5. --help ───────────────────────────────────────────────────────────────

@test "claude-review --help exits 0" {
  run "$TARBALL_ROOT/bin/claude-review" --help
  [ "$status" -eq 0 ]
}

# ── 6. --version ────────────────────────────────────────────────────────────

@test "claude-review --version exits 0" {
  run "$TARBALL_ROOT/bin/claude-review" --version
  [ "$status" -eq 0 ]
  [[ "$output" == *"claude-review"* ]]
}

@test "claude-review version reads VERSION file in tarball context" {
  run "$TARBALL_ROOT/bin/claude-review" --version
  [ "$status" -eq 0 ]
  [[ "$output" == *"$TEST_VERSION"* ]]
}

# ── 7. review-orchestrate Python imports ────────────────────────────────────

@test "review-orchestrate Python imports succeed from tarball layout" {
  run python3 -c "
import sys
sys.path.insert(0, '$TARBALL_ROOT/lib')
import review_common
import review_findings
import review_preflight
import review_prompt
import review_agent
import review_pipeline
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
import review_common
import review_findings
import review_dedup
import review_format
import review_github
import review_posting
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
