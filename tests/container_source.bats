#!/usr/bin/env bats
# Cross-validates the two definitions of "which worktree speaks for a container"
# (SSOT guard):
#   bin/resolve-worktree        — bash, redirects a session launched at the container
#   lib/permission_mirror.py    — Python, picks the worktree a mirror is copied from
#
# They answer the same question for opposite halves of one mechanism, so a
# divergence is silent and costs exactly what the mirror exists to prevent: the
# launch redirect drops a session into a worktree the mirror never wrote grants
# from, and every one of that repo's scripts prompts again with no error to say
# why. Both halves are independently correct in that state, which is why only a
# test comparing them catches it.
#
# The rule is the branch the container's own HEAD names. It is spelled twice
# because one caller is a shell prompt and a Stop hook and the other is the
# mirror — the same reason lib/roots.sh and ai/lib/core/workbench_paths.py both
# spell the workbench roots, guarded the same way by tests/workbench_roots.bats.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1

  # Physical path: on macOS mktemp hands back /var/..., git reports the
  # /private/var/... it resolves to, and every path comparison below would fail.
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  SEED="$TMPDIR/seed"
  CONTAINER="$TMPDIR/container"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# ─── Fixtures ───────────────────────────────────────────────────────────────

# _make_seed BRANCH — a one-commit repo to clone containers from.
_make_seed() {
  git init -q --initial-branch="$1" "$SEED"
  git -C "$SEED" config user.email test@example.com
  git -C "$SEED" config user.name Test
  printf 'seed\n' > "$SEED/README.md"
  git -C "$SEED" add -A
  git -C "$SEED" commit -qm init
}

# _make_container — a bare .git with no worktrees yet, the layout wt-init
# produces: container/.git bare, worktrees added as its peers.
_make_container() {
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
}

# _add_worktree BRANCH — branch off the seed's tip and check it out as a peer.
_add_worktree() {
  git -C "$CONTAINER" rev-parse --verify "$1" >/dev/null 2>&1 \
    || git -C "$CONTAINER" branch "$1" HEAD
  git -C "$CONTAINER" worktree add -q "$CONTAINER/$1" "$1"
}

# ─── Resolvers under test ───────────────────────────────────────────────────

# resolve_bash — the worktree bin/resolve-worktree picks, or '' when it cannot.
resolve_bash() {
  "$REPO_ROOT/bin/resolve-worktree" "$CONTAINER" 2>/dev/null || printf ''
}

# resolve_python MEMBER... — the worktree permission_mirror.source_of picks
# among MEMBERs, or '' when none of them is the one.
resolve_python() {
  python3 - "$REPO_ROOT" "$CONTAINER" "$@" <<'PY'
import os
import sys

sys.path.insert(0, os.path.join(sys.argv[1], 'lib'))
import permission_mirror

print(permission_mirror.source_of(sys.argv[2], list(sys.argv[3:])) or '', end='')
PY
}

# assert_agree WHAT EXPECTED MEMBER... — both resolvers answer EXPECTED.
# EXPECTED is '' when neither should be able to pick one.
assert_agree() {
  local what="$1" expected="$2"
  shift 2

  local from_bash from_python
  from_bash="$(resolve_bash)"
  from_python="$(resolve_python "$@")"

  if [[ "$from_bash" != "$expected" ]]; then
    echo "$what: resolve-worktree gave '$from_bash', expected '$expected'" >&2
    return 1
  fi
  if [[ "$from_python" != "$expected" ]]; then
    echo "$what: permission_mirror gave '$from_python', expected '$expected'" >&2
    return 1
  fi
}

# ─── Agreement ──────────────────────────────────────────────────────────────

@test "both pick the HEAD-branch worktree of an ordinary container" {
  _make_seed main
  _make_container
  _add_worktree main

  assert_agree "plain main" "$CONTAINER/main" "$CONTAINER/main"
}

@test "both ignore worktrees on branches HEAD does not name" {
  _make_seed main
  _make_container
  _add_worktree main
  _add_worktree feature

  assert_agree "main beside a feature branch" \
    "$CONTAINER/main" "$CONTAINER/main" "$CONTAINER/feature"
}

@test "both follow HEAD to a branch named neither main nor master" {
  # The shape that separates reading HEAD from guessing at the common names:
  # `main` exists and holds a worktree, and is the wrong answer.
  _make_seed main
  _make_container
  _add_worktree main
  _add_worktree trunk
  git -C "$CONTAINER" symbolic-ref HEAD refs/heads/trunk

  assert_agree "HEAD on trunk" \
    "$CONTAINER/trunk" "$CONTAINER/main" "$CONTAINER/trunk"
}

@test "both follow HEAD on a master-default container" {
  _make_seed master
  _make_container
  _add_worktree master

  assert_agree "master default" "$CONTAINER/master" "$CONTAINER/master"
}

@test "neither is moved by an origin/HEAD that disagrees with HEAD" {
  # A remote's published default is not the container's own. Reading it was the
  # rule bin/resolve-worktree used before the two were converged, and it is the
  # concrete way they came apart: this container would have redirected to trunk
  # and mirrored from main.
  _make_seed main
  _make_container
  _add_worktree main
  _add_worktree trunk
  git -C "$CONTAINER" symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/trunk

  assert_agree "origin/HEAD on trunk, HEAD on main" \
    "$CONTAINER/main" "$CONTAINER/main" "$CONTAINER/trunk"
}

@test "both decline a container with no worktree on the HEAD branch" {
  _make_seed main
  _make_container
  _add_worktree feature

  assert_agree "feature branches only" "" "$CONTAINER/feature"
}

@test "both decline a container with no worktrees at all" {
  _make_seed main
  _make_container

  assert_agree "empty container" ""
}
