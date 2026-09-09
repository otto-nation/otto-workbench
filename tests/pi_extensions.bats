#!/usr/bin/env bats
# Tests for step_pi_extensions — the Pi extension installer.
#
# The subject is filesystem reconciliation, so this mirrors skills_install.bats
# rather than pi_settings.bats: a fake workbench tree, the real libraries
# sourced against it, and assertions about what survives a prune.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"

  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_SYNC=true
  mkdir -p "$HOME"

  FAKE_WORKBENCH="$TMPDIR/workbench"
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions"
  cp "$REPO_ROOT/ai/pi/steps.sh" "$FAKE_WORKBENCH/ai/pi/steps.sh"

  PI_EXT_DIR="$HOME/.pi/agent/extensions"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# _make_extension NAME [ENTRY] — writes a minimal extension into the fake tree.
# ENTRY defaults to index.ts; pass another name to test Pi's other entry points.
_make_extension() {
  local name="$1" entry="${2:-index.ts}"
  local dir="$FAKE_WORKBENCH/ai/pi/extensions/$name"
  mkdir -p "$dir"
  printf 'export default function (pi) {}\n' > "$dir/$entry"
}

# _make_override NAME [ENTRY] — the same, in the operator's override layer.
_make_override() {
  local name="$1" entry="${2:-index.ts}"
  local dir="$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions/$name"
  mkdir -p "$dir"
  printf 'export default function (pi) { /* override */ }\n' > "$dir/$entry"
}

# _make_handwritten NAME — an extension the operator wrote directly into Pi's
# discovery root, which the workbench never installed and must never remove.
_make_handwritten() {
  local dir="$PI_EXT_DIR/$1"
  mkdir -p "$dir"
  printf 'export default function (pi) { /* mine */ }\n' > "$dir/index.ts"
}

# _run_step — sources the real libraries against the fake workbench.
#
# `set -e` matches every production caller, and WORKBENCH_STABLE_DIR is pinned
# to the fake tree because lib/constants.sh only derives it when unset — the
# real checkout's value would otherwise leak in from the environment and
# install_symlink would rewrite every source path back to it.
_run_step() {
  run bash -c "
    set -e
    export WORKBENCH_DIR='$FAKE_WORKBENCH'
    export WORKBENCH_STABLE_DIR='$FAKE_WORKBENCH'
    . '$REPO_ROOT/lib/ui.sh'
    . '$FAKE_WORKBENCH/ai/pi/steps.sh'
    step_pi_extensions
  "
}

# _run_step_from_worktree — the everyday configuration on a worktree machine,
# where WORKBENCH_STABLE_DIR names a different tree from WORKBENCH_DIR.
#
# install_symlink rewrites each source path through the stable dir, so here no
# installed symlink points at WORKBENCH_DIR at all. Pinning the two together
# hides whether ownership is decided against the path actually written.
_run_step_from_worktree() {
  run bash -c "
    set -e
    export WORKBENCH_DIR='$FAKE_WORKBENCH'
    export WORKBENCH_STABLE_DIR='$TMPDIR/main'
    . '$REPO_ROOT/lib/ui.sh'
    . '$FAKE_WORKBENCH/ai/pi/steps.sh'
    step_pi_extensions
  "
}

# ─── Installing ─────────────────────────────────────────────────────────────

@test "an extension is installed into Pi's discovery root" {
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ -L "$PI_EXT_DIR/capture" ]
}

@test "the installed symlink points at the source directory" {
  _make_extension capture
  _run_step
  [ "$(readlink "$PI_EXT_DIR/capture")" = "$FAKE_WORKBENCH/ai/pi/extensions/capture" ]
}

@test "the entry point is reachable through the symlink" {
  _make_extension capture
  _run_step
  [ -f "$PI_EXT_DIR/capture/index.ts" ]
}

@test "an extension with an index.js is installed" {
  _make_extension compiled index.js
  _run_step
  [ -L "$PI_EXT_DIR/compiled" ]
}

@test "an extension declaring its own entry points in package.json is installed" {
  _make_extension packaged package.json
  _run_step
  [ -L "$PI_EXT_DIR/packaged" ]
}

@test "a directory with no entry point is skipped rather than installed" {
  # What the skip itself does: the entry-less directory is not installed, and
  # the step still succeeds rather than treating it as fatal.
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions/empty"
  _make_extension real
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/empty" ]
  [ -L "$PI_EXT_DIR/real" ]
}

@test "a malformed extension does not stop the others installing" {
  # What the skip does to the *loop*: `continue` rather than `return`, so
  # extensions queued after the bad one are still reached. The test above
  # would pass even if the loop aborted, since it has only one good entry.
  mkdir -p "$FAKE_WORKBENCH/ai/pi/extensions/empty"
  _make_extension one
  _make_extension two
  _run_step
  [ "$status" -eq 0 ]
  [ -L "$PI_EXT_DIR/one" ]
  [ -L "$PI_EXT_DIR/two" ]
}

@test "a second run changes nothing" {
  _make_extension capture
  _run_step
  local before
  before="$(readlink "$PI_EXT_DIR/capture")"
  _run_step
  [ "$status" -eq 0 ]
  [ "$(readlink "$PI_EXT_DIR/capture")" = "$before" ]
}

@test "an absent source tree is skipped, not an error" {
  rm -rf "$FAKE_WORKBENCH/ai/pi/extensions"
  _run_step
  [ "$status" -eq 0 ]
}

# ─── Overrides ──────────────────────────────────────────────────────────────

@test "an override replaces the shipped extension of the same name" {
  _make_extension capture
  _make_override capture
  _run_step
  [ "$(readlink "$PI_EXT_DIR/capture")" \
    = "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions/capture" ]
}

@test "an override adds an extension the workbench does not ship" {
  _make_override mine
  _run_step
  [ -L "$PI_EXT_DIR/mine" ]
}

@test "a .disabled sentinel suppresses a shipped extension" {
  _make_extension capture
  mkdir -p "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions"
  touch "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions/capture.disabled"
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/capture" ]
}

@test "the sentinel is spelled without the entry point's extension" {
  # resolve_layers keys a "*/" glob on the directory name, which is what makes
  # <name>.disabled work here as it does for skills. A flat <name>.ts layout
  # would key on "capture.ts" and this sentinel would silently do nothing.
  _make_extension capture
  mkdir -p "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions"
  touch "$WORKBENCH_CONFIG_DIR/overrides/ai/pi/extensions/capture.ts.disabled"
  _run_step
  [ -L "$PI_EXT_DIR/capture" ]
}

# ─── Pruning ────────────────────────────────────────────────────────────────

@test "an extension removed from the tree is pruned" {
  _make_extension capture
  _run_step
  rm -rf "$FAKE_WORKBENCH/ai/pi/extensions/capture"
  _make_extension other
  _run_step
  [ "$status" -eq 0 ]
  [ ! -L "$PI_EXT_DIR/capture" ]
}

@test "pruning a retired extension removes the dangling symlink itself" {
  # The prune loop globs "$target"/* rather than "$target"/*/ for this: the
  # trailing-slash form only matches entries that resolve as directories, so a
  # dangling link would never be visited and -e alone would pass while the
  # broken link stayed.
  _make_extension capture
  _run_step
  rm -rf "$FAKE_WORKBENCH/ai/pi/extensions/capture"
  _make_extension other
  _run_step
  [ ! -L "$PI_EXT_DIR/capture" ]
  [ ! -e "$PI_EXT_DIR/capture" ]
}

@test "an extension installed from a worktree is pruned once its source is gone" {
  mkdir -p "$TMPDIR/main"
  _make_extension capture
  _run_step_from_worktree
  [ -L "$PI_EXT_DIR/capture" ]
  rm -rf "$FAKE_WORKBENCH/ai/pi/extensions/capture"
  _make_extension other
  _run_step_from_worktree
  [ ! -L "$PI_EXT_DIR/capture" ]
}

# ─── Ownership ──────────────────────────────────────────────────────────────

@test "a hand-written extension directory survives a prune" {
  # ~/.pi/agent/extensions is where Pi's own docs tell an operator to put one,
  # so a real directory here is always theirs.
  _make_handwritten mine
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ -f "$PI_EXT_DIR/mine/index.ts" ]
}

@test "a hand-written extension is reported rather than removed silently" {
  _make_handwritten mine
  _make_extension capture
  _run_step
  [[ "$output" == *"was not installed by the workbench"* ]]
}

@test "a symlink pointing outside the workbench survives a prune" {
  mkdir -p "$TMPDIR/elsewhere/extensions/theirs"
  mkdir -p "$PI_EXT_DIR"
  ln -s "$TMPDIR/elsewhere/extensions/theirs" "$PI_EXT_DIR/theirs"
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ -L "$PI_EXT_DIR/theirs" ]
}

@test "a symlink into the checkout but outside an extensions dir survives" {
  # The extensions/ path segment is what stops ownership swallowing the whole
  # checkout: a hand-placed link to a note or a doc is not something this step
  # ever wrote.
  mkdir -p "$FAKE_WORKBENCH/docs"
  printf 'notes\n' > "$FAKE_WORKBENCH/docs/notes.md"
  mkdir -p "$PI_EXT_DIR"
  ln -s "$FAKE_WORKBENCH/docs/notes.md" "$PI_EXT_DIR/notes.md"
  _make_extension capture
  _run_step
  [ -L "$PI_EXT_DIR/notes.md" ]
}

@test "a plain file in the discovery root survives a prune" {
  mkdir -p "$PI_EXT_DIR"
  printf 'export default function (pi) {}\n' > "$PI_EXT_DIR/theirs.ts"
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ -f "$PI_EXT_DIR/theirs.ts" ]
}

# ─── What is deliberately not installed ─────────────────────────────────────

@test "the real tree's extensions-cli is not installed globally" {
  # review-guard is passed with --extension for review runs only. Installed
  # into the discovery root it would load in every interactive session, where
  # REVIEW_WORKTREE_DIR is unset.
  [ -f "$REPO_ROOT/ai/pi/extensions-cli/review-guard.ts" ]
  [ ! -e "$REPO_ROOT/ai/pi/extensions/review-guard.ts" ]
  [ ! -d "$REPO_ROOT/ai/pi/extensions/review-guard" ]
}

@test "the README beside the extensions is not installed as one" {
  printf '# notes\n' > "$FAKE_WORKBENCH/ai/pi/extensions/README.md"
  _make_extension capture
  _run_step
  [ "$status" -eq 0 ]
  [ ! -e "$PI_EXT_DIR/README.md" ]
}

@test "every extension in the real tree has an entry point Pi can load" {
  for dir in "$REPO_ROOT"/ai/pi/extensions/*/; do
    [ -d "$dir" ] || continue
    [ -f "${dir}index.ts" ] || [ -f "${dir}index.js" ] || [ -f "${dir}package.json" ]
  done
}
