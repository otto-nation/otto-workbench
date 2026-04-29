#!/usr/bin/env bats
# Tests for bin/local/validate-components.
# Covers both the Tier 1 (steps.sh → sync_<name>) and Tier 2 (registry) contracts.

setup() {
  load 'test_helper'
  common_setup
  SCRIPT="$REPO_ROOT/bin/local/validate-components"
  TMPDIR="$(mktemp -d)"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# ─── Smoke test against the real repo ─────────────────────────────────────────

@test "passes against the current repo" {
  run bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

# ─── Tier 1: steps.sh must define sync_<name>() ───────────────────────────────

@test "fails when steps.sh is missing sync_<name>()" {
  # Set up a fake workbench dir with one steps.sh that lacks sync_mycomp()
  local fake_root="$TMPDIR/workbench"
  mkdir -p "$fake_root/mycomp"
  printf '#!/bin/bash\nstep_mycomp() { :; }\n' > "$fake_root/mycomp/steps.sh"

  # Create a minimal lib/ui.sh so the script can source it
  mkdir -p "$fake_root/lib"
  printf '#!/bin/bash\nerr() { echo "✗ $*" >&2; }\nsuccess() { echo "✓ $*"; }\ninfo() { echo "→ $*"; }\nWORKBENCH_DIR="%s"\n' "$fake_root" > "$fake_root/lib/ui.sh"

  # Patch WORKBENCH_DIR for this invocation
  run env WORKBENCH_DIR="$fake_root" bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"mycomp/steps.sh is missing sync_mycomp()"* ]]
}

@test "passes when steps.sh defines sync_<name>()" {
  local fake_root="$TMPDIR/workbench"
  mkdir -p "$fake_root/mycomp"
  printf '#!/bin/bash\nsync_mycomp() { :; }\n' > "$fake_root/mycomp/steps.sh"
  # Empty registry — no optional components registered
  touch "$fake_root/install.components"

  mkdir -p "$fake_root/lib"
  printf '#!/bin/bash\nerr() { echo "✗ $*" >&2; }\nsuccess() { echo "✓ $*"; }\ninfo() { echo "→ $*"; }\nWORKBENCH_DIR="%s"\n' "$fake_root" > "$fake_root/lib/ui.sh"

  run env WORKBENCH_DIR="$fake_root" bash "$SCRIPT"
  [ "$status" -eq 0 ]
}

# ─── Tier 2: optional component contracts ─────────────────────────────────────

@test "fails when registered component is missing setup.sh" {
  local fake_root="$TMPDIR/workbench"
  mkdir -p "$fake_root/myopt"
  printf 'label = My opt\ndescription = An optional component\n' > "$fake_root/myopt/setup.conf"
  # Intentionally NO setup.sh
  echo "myopt" > "$fake_root/install.components"

  mkdir -p "$fake_root/lib"
  printf '#!/bin/bash\nerr() { echo "✗ $*" >&2; }\nsuccess() { echo "✓ $*"; }\ninfo() { echo "→ $*"; }\nWORKBENCH_DIR="%s"\n' "$fake_root" > "$fake_root/lib/ui.sh"

  run env WORKBENCH_DIR="$fake_root" bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"myopt: missing setup.sh"* ]]
}

@test "fails when optional component steps.sh lacks sync_<name>()" {
  local fake_root="$TMPDIR/workbench"
  mkdir -p "$fake_root/myopt"
  printf 'label = My opt\ndescription = An optional component\n' > "$fake_root/myopt/setup.conf"
  printf '#!/bin/bash\n' > "$fake_root/myopt/setup.sh"
  # steps.sh exists but is missing sync_myopt()
  printf '#!/bin/bash\nstep_myopt() { :; }\n' > "$fake_root/myopt/steps.sh"
  echo "myopt" > "$fake_root/install.components"

  mkdir -p "$fake_root/lib"
  printf '#!/bin/bash\nerr() { echo "✗ $*" >&2; }\nsuccess() { echo "✓ $*"; }\ninfo() { echo "→ $*"; }\nWORKBENCH_DIR="%s"\n' "$fake_root" > "$fake_root/lib/ui.sh"

  run env WORKBENCH_DIR="$fake_root" bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"myopt/steps.sh is missing sync_myopt()"* ]]
}

@test "fails on orphaned setup.conf not in install.components" {
  local fake_root="$TMPDIR/workbench"
  mkdir -p "$fake_root/orphan"
  printf 'label = Orphan\ndescription = Not registered\n' > "$fake_root/orphan/setup.conf"
  printf '#!/bin/bash\n' > "$fake_root/orphan/setup.sh"
  # install.components exists but does NOT list orphan
  touch "$fake_root/install.components"

  mkdir -p "$fake_root/lib"
  printf '#!/bin/bash\nerr() { echo "✗ $*" >&2; }\nsuccess() { echo "✓ $*"; }\ninfo() { echo "→ $*"; }\nWORKBENCH_DIR="%s"\n' "$fake_root" > "$fake_root/lib/ui.sh"

  run env WORKBENCH_DIR="$fake_root" bash "$SCRIPT"
  [ "$status" -eq 1 ]
  [[ "$output" == *"orphan: has setup.conf but is not registered"* ]]
}
