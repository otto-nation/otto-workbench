#!/usr/bin/env bats
# bin/local/installed-copy-check, and the guards that refuse to edit the
# installed copy or to make a worktree from it.
#
# The checker owns the fact; each guard is checked against it, and the last
# cases check the two harnesses against each other, since a write refused under
# one and accepted under the other reads as enforced in both.
#
# Claude halves: ai/claude/bin/claude-edit-guard, ai/claude/bin/claude-bash-guard.
# Pi half: ai/pi/extensions/installed-copy-guard/detect.ts.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  export HOME="$TMPDIR/home"
  INSTALL="$TMPDIR/install"
  OTHER="$TMPDIR/other"
  export WORKBENCH_INSTALL_DIR="$INSTALL"
  mkdir -p "$HOME"

  _make_repo "$INSTALL"
  _make_repo "$OTHER"
  git -C "$INSTALL" worktree add -q -b feature "$TMPDIR/install-feature"
  git -C "$OTHER" worktree add -q -b feature "$TMPDIR/other-feature"
}

teardown() {
  common_teardown
}

_make_repo() {
  git init -q --initial-branch=main "$1"
  git -C "$1" config user.email test@example.com
  git -C "$1" config user.name Test
  printf 'seed\n' > "$1/README.md"
  git -C "$1" add -A
  git -C "$1" commit -qm init
}

# 0 to allow, 2 to refuse.
_claude_edit() {
  printf '{"tool_input":{"file_path":"%s"}}' "$1" \
    | "$REPO_ROOT/ai/claude/bin/claude-edit-guard" >/dev/null 2>&1
}

# _claude_bash CWD COMMAND — 0 to allow, 2 to refuse.
_claude_bash() {
  jq -n --arg cwd "$1" --arg cmd "$2" '{cwd: $cwd, tool_input: {command: $cmd}}' \
    | "$REPO_ROOT/ai/claude/bin/claude-bash-guard" >/dev/null 2>&1
}

_pi_edit() {
  node --input-type=module -e "
    import { editRefusal } from '$REPO_ROOT/ai/pi/extensions/installed-copy-guard/detect.ts';
    process.exit(editRefusal(process.argv[1], '/') ? 2 : 0);
  " "$1" >/dev/null 2>&1
}

_pi_bash() {
  node --input-type=module -e "
    import { commandRefusal } from '$REPO_ROOT/ai/pi/extensions/installed-copy-guard/detect.ts';
    process.exit(commandRefusal(process.argv[1], process.argv[2]) ? 2 : 0);
  " "$1" "$2" >/dev/null 2>&1
}

# ─── The checker ────────────────────────────────────────────────────────────

@test "the checker says yes for the install, its worktree, and a file not yet written" {
  run "$REPO_ROOT/bin/local/installed-copy-check" "$INSTALL"
  [ "$status" -eq 0 ]
  run "$REPO_ROOT/bin/local/installed-copy-check" "$TMPDIR/install-feature"
  [ "$status" -eq 0 ]
  run "$REPO_ROOT/bin/local/installed-copy-check" "$TMPDIR/install-feature/new/dir/file.md"
  [ "$status" -eq 0 ]
}

@test "the checker says no for another repository and its worktree" {
  run "$REPO_ROOT/bin/local/installed-copy-check" "$OTHER"
  [ "$status" -eq 1 ]
  run "$REPO_ROOT/bin/local/installed-copy-check" "$TMPDIR/other-feature"
  [ "$status" -eq 1 ]
}

@test "the checker says no when there is no install" {
  export WORKBENCH_INSTALL_DIR="$TMPDIR/absent"
  run "$REPO_ROOT/bin/local/installed-copy-check" "$OTHER"
  [ "$status" -eq 1 ]
}

@test "the checker says no for a git directory exported by a calling hook" {
  GIT_DIR="$OTHER/.git" run "$REPO_ROOT/bin/local/installed-copy-check" "$OTHER"
  [ "$status" -eq 1 ]
  GIT_DIR="$OTHER/.git" run "$REPO_ROOT/bin/local/installed-copy-check" "$INSTALL"
  [ "$status" -eq 0 ]
}

@test "--creating matches creations only, in any statement of the command" {
  local dir="$TMPDIR/install-feature" c
  for c in "wt switch -c x" "wt switch --create x --base y" "ls && git worktree add ../x" \
           "git -C /a worktree add b"; do
    run "$REPO_ROOT/bin/local/installed-copy-check" --creating "$c" "$dir"
    [ "$status" -eq 0 ] || { echo "should match: $c"; return 1; }
  done
  for c in "wt switch main" "git worktree list" "echo wt switch -c" "ls"; do
    run "$REPO_ROOT/bin/local/installed-copy-check" --creating "$c" "$dir"
    [ "$status" -eq 1 ] || { echo "should not match: $c"; return 1; }
  done
}

@test "--creating without a command is a usage error" {
  run "$REPO_ROOT/bin/local/installed-copy-check" --creating
  [ "$status" -eq 64 ]
}

# ─── Edits ──────────────────────────────────────────────────────────────────

@test "both harnesses refuse an edit inside a worktree of the install" {
  run _claude_edit "$TMPDIR/install-feature/ai/new.sh"
  [ "$status" -eq 2 ]
  run _pi_edit "$TMPDIR/install-feature/ai/new.sh"
  [ "$status" -eq 2 ]
}

@test "both harnesses allow an edit in another repository's worktree" {
  run _claude_edit "$TMPDIR/other-feature/ai/new.sh"
  [ "$status" -eq 0 ]
  run _pi_edit "$TMPDIR/other-feature/ai/new.sh"
  [ "$status" -eq 0 ]
}

# ─── Worktree creation ──────────────────────────────────────────────────────

@test "both harnesses refuse wt switch -c from the install" {
  run _claude_bash "$INSTALL" "wt switch -c isaac/fix/x"
  [ "$status" -eq 2 ]
  run _pi_bash "wt switch -c isaac/fix/x" "$INSTALL"
  [ "$status" -eq 2 ]
}

@test "both harnesses allow wt switch -c from another repository, and a plain switch from the install" {
  run _claude_bash "$OTHER" "wt switch -c isaac/fix/x"
  [ "$status" -eq 0 ]
  run _pi_bash "wt switch -c isaac/fix/x" "$OTHER"
  [ "$status" -eq 0 ]
  run _claude_bash "$INSTALL" "wt switch main"
  [ "$status" -eq 0 ]
  run _pi_bash "wt switch main" "$INSTALL"
  [ "$status" -eq 0 ]
}
