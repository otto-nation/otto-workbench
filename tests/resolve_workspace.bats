#!/usr/bin/env bats
# bin/resolve-workspace, and the two guards that refuse a plan or spec written
# anywhere but the path it prints.
#
# The guarantee has three parts and each fails silently on its own. The
# resolver can name the wrong directory; a guard can fail open and refuse
# nothing; and the two guards can disagree, which is worse than either — a
# write refused under one harness and accepted under the other puts the
# artifact in the checkout for half the sessions and reads as enforced in both.
# So the resolver is checked against the layout, each guard is checked against
# the resolver, and the last case here checks the guards against each other.
#
# The Claude half is ai/claude/bin/claude-edit-guard (stdin JSON, `file_path`);
# the Pi half is ai/pi/extensions/workspace-guard/detect.ts (argument, `path`).
# Both shell out to bin/resolve-workspace, which is the single owner of where
# the artifact belongs.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1

  # Physical path: on macOS mktemp hands back /var/..., git reports the
  # /private/var/... it resolves to, and every path comparison below fails.
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  SEED="$TMPDIR/seed"
  CONTAINER="$TMPDIR/container"

  _make_seed main
}

teardown() {
  common_teardown
}

# ─── Fixtures ───────────────────────────────────────────────────────────────

_make_seed() {
  git init -q --initial-branch="$1" "$SEED"
  git -C "$SEED" config user.email test@example.com
  git -C "$SEED" config user.name Test
  printf 'seed\n' > "$SEED/README.md"
  git -C "$SEED" add -A
  git -C "$SEED" commit -qm init
}

# _make_container — the layout wt-init produces: a bare .git with worktrees
# added as its peers.
_make_container() {
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
}

_add_worktree() {
  git -C "$CONTAINER" rev-parse --verify "$1" >/dev/null 2>&1 \
    || git -C "$CONTAINER" branch "$1" HEAD
  git -C "$CONTAINER" worktree add -q "$CONTAINER/$1" "$1"
}

# ─── The guards, each asked about one path ──────────────────────────────────

# _claude_guard PATH — 0 to allow, 2 to refuse.
_claude_guard() {
  printf '{"tool_input":{"file_path":"%s"}}' "$1" \
    | "$REPO_ROOT/ai/claude/bin/claude-edit-guard" >/dev/null 2>&1
}

# _pi_guard PATH — 0 to allow, 2 to refuse, matching the Claude half's codes so
# the cross-check below can compare them directly.
_pi_guard() {
  node --input-type=module -e "
    import { workspaceRefusal } from '$REPO_ROOT/ai/pi/extensions/workspace-guard/detect.ts';
    process.exit(workspaceRefusal(process.argv[1]) ? 2 : 0);
  " "$1" >/dev/null 2>&1
}

# ─── The resolver ───────────────────────────────────────────────────────────

@test "resolve-workspace names the container's workspace from inside a worktree" {
  _make_container
  _add_worktree feature

  run "$REPO_ROOT/bin/resolve-workspace" "$CONTAINER/feature"

  [ "$status" -eq 0 ]
  [ "$output" = "$CONTAINER/workspace" ]
}

@test "resolve-workspace gives every worktree of one repo the same answer" {
  _make_container
  _add_worktree one
  _add_worktree two

  run "$REPO_ROOT/bin/resolve-workspace" "$CONTAINER/one"
  local first="$output"
  run "$REPO_ROOT/bin/resolve-workspace" "$CONTAINER/two"

  [ "$output" = "$first" ]
}

@test "resolve-workspace refuses an ordinary clone rather than naming a path" {
  # --separate-stderr because the assertion is that *stdout* is empty: a
  # caller does `ws=$(resolve-workspace)` and a diagnostic on stdout would be
  # captured as the path.
  run --separate-stderr "$REPO_ROOT/bin/resolve-workspace" "$SEED"

  [ "$status" -eq 1 ]
  [ -z "$output" ]
}

@test "resolve-workspace refusal says how to fix it" {
  run --separate-stderr "$REPO_ROOT/bin/resolve-workspace" "$SEED"

  [[ "$stderr" == *"wt-init"* ]]
}

@test "resolve-workspace is not fooled by an inherited GIT_DIR" {
  # A sync or a hook exports GIT_DIR, and git reads it ahead of any directory
  # it is handed — so without the clear the answer is the hook's repository,
  # arriving as a success.
  _make_container
  _add_worktree feature

  GIT_DIR="$SEED/.git" run "$REPO_ROOT/bin/resolve-workspace" "$CONTAINER/feature"

  [ "$status" -eq 0 ]
  [ "$output" = "$CONTAINER/workspace" ]
}

# ─── The Claude guard ───────────────────────────────────────────────────────

@test "claude-edit-guard refuses a spec written inside a worktree" {
  _make_container
  _add_worktree feature

  run _claude_guard "$CONTAINER/feature/docs/superpowers/specs/design.md"

  [ "$status" -eq 2 ]
}

@test "claude-edit-guard refuses a spec whose directory does not exist yet" {
  # The ordinary case, not an edge one: the first spec a repo gets creates its
  # directory with it. Resolving the repo by cd-ing into the file's own parent
  # fails for every such write, and the guard then exits 0 — failing open on
  # exactly what it is for.
  _make_container
  _add_worktree feature
  [ ! -d "$CONTAINER/feature/workspace" ]

  run _claude_guard "$CONTAINER/feature/workspace/plans/new.md"

  [ "$status" -eq 2 ]
}

@test "claude-edit-guard allows the write at the container's workspace" {
  _make_container
  _add_worktree feature

  run _claude_guard "$CONTAINER/workspace/specs/design.md"

  [ "$status" -eq 0 ]
}

@test "claude-edit-guard names the resolver's path in its refusal" {
  _make_container
  _add_worktree feature

  run bash -c "printf '{\"tool_input\":{\"file_path\":\"$CONTAINER/feature/ignore/plans/p.md\"}}' \
    | '$REPO_ROOT/ai/claude/bin/claude-edit-guard' 2>&1"

  [[ "$output" == *"$CONTAINER/workspace"* ]]
}

@test "claude-edit-guard leaves a lookalike directory name alone" {
  # Segment matching, not substring: `my-workspace/` is an ordinary directory.
  _make_container
  _add_worktree feature

  run _claude_guard "$CONTAINER/feature/my-workspace/notes.md"

  [ "$status" -eq 0 ]
}

@test "claude-edit-guard leaves ordinary source files alone" {
  _make_container
  _add_worktree feature

  run _claude_guard "$CONTAINER/feature/README.md"

  [ "$status" -eq 0 ]
}

# ─── The two guards agree ───────────────────────────────────────────────────

@test "both guards reach the same verdict on every path" {
  # The divergence this exists for is silent: a write refused under one
  # harness and accepted under the other lands in the checkout for half the
  # sessions while reading as enforced in both.
  _make_container
  _add_worktree feature
  local wt="$CONTAINER/feature"

  local paths=(
    "$wt/docs/superpowers/specs/d.md"
    "$wt/workspace/plans/p.md"
    "$wt/ignore/plans/p.md"
    "$wt/ignore/specs/s.md"
    "$wt/my-workspace/notes.md"
    "$wt/README.md"
    "$CONTAINER/workspace/specs/d.md"
  )

  local path claude pi
  for path in "${paths[@]}"; do
    claude=0; _claude_guard "$path" || claude=$?
    pi=0;     _pi_guard "$path"     || pi=$?
    [ "$claude" -eq "$pi" ] || {
      echo "disagreement on $path: claude=$claude pi=$pi" >&2
      return 1
    }
  done
}
