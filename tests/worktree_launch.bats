#!/usr/bin/env bats
# Tests for zsh/config.d/tools/{claude,pi}.zsh and the _worktree_launch.zsh
# helper they share — the launch wrappers that redirect a bare-repo container
# into the worktree it stands in for. Every case runs against both wrappers:
# they are one rule bound to two names, and a case that held for only one of
# them would be the two harnesses drifting apart.

bats_require_minimum_version 1.5.0

WRAPPERS=(claude pi)

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1

  SNIPPET_DIR="$REPO_ROOT/zsh/config.d/tools"
  # Physical path: on macOS mktemp hands back /var/..., git reports the
  # /private/var/... it resolves to, and every path comparison below would fail.
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  SEED="$TMPDIR/seed"
  CONTAINER="$TMPDIR/container"
  FAKE_BIN="$TMPDIR/bin"
  mkdir -p "$FAKE_BIN"
}

teardown() {
  common_teardown
}

# _make_seed — a one-commit repo to clone containers from.
_make_seed() {
  git init -q --initial-branch=main "$SEED"
  git -C "$SEED" config user.email test@example.com
  git -C "$SEED" config user.name Test
  printf 'seed\n' > "$SEED/README.md"
  git -C "$SEED" add -A
  git -C "$SEED" commit -qm init
}

# _make_container — container/.git bare with a worktree on main beside it.
_make_container() {
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
  git -C "$CONTAINER" worktree add -q "$CONTAINER/main" main
}

# _fake CMD [EXIT] — a CMD on PATH that reports where it ran and with what.
_fake() {
  local cmd="$1" code="${2:-0}"
  cat > "$FAKE_BIN/$cmd" <<SCRIPT
#!/usr/bin/env bash
printf 'LAUNCHED=%s IN=%s\n' "$cmd" "\$(pwd -P)"
printf 'ARGS=%s\n' "\$*"
exit $code
SCRIPT
  chmod +x "$FAKE_BIN/$cmd"
}

# _fake_all [EXIT] — fake every wrapped command.
_fake_all() {
  local cmd
  for cmd in "${WRAPPERS[@]}"; do _fake "$cmd" "${1:-0}"; done
}

# _launch CMD DIR [ARGS...] — source CMD's snippet in DIR and call the wrapper
# there, then report the shell's own directory so a leaked `cd` is visible.
_launch() {
  local cmd="$1" dir="$2"
  shift 2
  PATH="$FAKE_BIN:$REPO_ROOT/bin:$PATH" run zsh -c "
    cd '$dir'
    source '$SNIPPET_DIR/$cmd.zsh'
    $cmd $*
    print -- \"WRAPPER_RC=\$?\"
    print -- \"SHELL_STAYED_IN=\$(pwd -P)\"
  "
}

# ── Redirected ───────────────────────────────────────────────────────────────

@test "launches in the worktree when started at a bare container" {
  _make_seed
  _make_container
  _fake_all

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    _launch "$cmd" "$CONTAINER"
    [ "$status" -eq 0 ]
    [[ "$output" == *"LAUNCHED=$cmd IN=$CONTAINER/main"* ]]
  done
}

@test "says where it redirected to, under the wrapped command's name" {
  _make_seed
  _make_container
  _fake_all

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    _launch "$cmd" "$CONTAINER"
    [[ "$output" == *"$cmd: $CONTAINER is a bare repository — launching in $CONTAINER/main"* ]]
  done
}

@test "leaves the calling shell in the container" {
  _make_seed
  _make_container
  _fake_all

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    _launch "$cmd" "$CONTAINER"
    [[ "$output" == *"SHELL_STAYED_IN=$CONTAINER"* ]]
    [[ "$output" != *"SHELL_STAYED_IN=$CONTAINER/main"* ]]
  done
}

@test "forwards arguments through the redirect" {
  _make_seed
  _make_container
  _fake_all

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    _launch "$cmd" "$CONTAINER" --resume "'a b'"
    [[ "$output" == *"ARGS=--resume a b"* ]]
  done
}

@test "returns the redirected session's exit status" {
  _make_seed
  _make_container
  _fake_all 3

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    _launch "$cmd" "$CONTAINER"
    [[ "$output" == *"WRAPPER_RC=3"* ]]
  done
}

# ── Passed through ───────────────────────────────────────────────────────────

@test "launches in place inside a worktree" {
  _make_seed
  _make_container
  _fake_all

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    _launch "$cmd" "$CONTAINER/main"
    [[ "$output" == *"LAUNCHED=$cmd IN=$CONTAINER/main"* ]]
    [[ "$output" != *"launching in"* ]]
  done
}

@test "launches in place in an ordinary repo" {
  _make_seed
  _fake_all

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    _launch "$cmd" "$SEED"
    [[ "$output" == *"LAUNCHED=$cmd IN=$SEED"* ]]
    [[ "$output" != *"launching in"* ]]
  done
}

@test "launches in place outside any repo" {
  local loose="$TMPDIR/loose"
  mkdir -p "$loose"
  _fake_all

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    _launch "$cmd" "$loose"
    [[ "$output" == *"LAUNCHED=$cmd IN=$loose"* ]]
    [[ "$output" != *"launching in"* ]]
  done
}

@test "passes through when resolve-worktree is not installed" {
  _make_seed
  _make_container
  _fake_all

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    # A PATH with no workbench bin dir on it — not even the installed one,
    # which would otherwise resolve and defeat the case.
    PATH="$FAKE_BIN:/usr/bin:/bin" run zsh -c "
      cd '$CONTAINER'
      source '$SNIPPET_DIR/$cmd.zsh'
      $cmd
    "
    [ "$status" -eq 0 ]
    [[ "$output" == *"LAUNCHED=$cmd IN=$CONTAINER"* ]]
  done
}

# ── Nothing to redirect to ───────────────────────────────────────────────────

@test "reports a container with no worktree instead of redirecting silently" {
  _make_seed
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
  _fake_all

  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    _launch "$cmd" "$CONTAINER"
    [[ "$output" == *"no worktree on 'main'"* ]]
    [[ "$output" == *"$cmd: cannot resolve a worktree for $CONTAINER"* ]]
    [[ "$output" == *"LAUNCHED=$cmd IN=$CONTAINER"* ]]
  done
}

# ── The shared helper ────────────────────────────────────────────────────────

@test "each wrapper loads the helper itself, whatever else was sourced" {
  # bats sources one wrapper alone; the zsh loader sources every snippet. The
  # wrapper must not depend on the loader having reached the helper first.
  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    run zsh -c "source '$SNIPPET_DIR/$cmd.zsh' && whence -w _wb_launch_in_worktree"
    [ "$status" -eq 0 ]
    [[ "$output" == *"function"* ]]
  done
}

@test "every launch wrapper delegates to the helper rather than restating it" {
  # A wrapper that grew its own copy of the logic would be the drift this
  # helper exists to end. resolve-worktree is named in the helper only.
  local cmd
  for cmd in "${WRAPPERS[@]}"; do
    grep -q "_wb_launch_in_worktree $cmd" "$SNIPPET_DIR/$cmd.zsh"
    ! grep -q '^[^#]*resolve-worktree' "$SNIPPET_DIR/$cmd.zsh"
  done
}
