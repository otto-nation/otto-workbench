#!/usr/bin/env bats
# Tests for zsh/config.d/tools/claude.zsh — the `claude` launch wrapper that
# redirects a bare-repo container into the worktree it stands in for.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  export NO_COLOR=1
  export GIT_CONFIG_GLOBAL=/dev/null

  SNIPPET="$REPO_ROOT/zsh/config.d/tools/claude.zsh"
  # Physical path: on macOS mktemp hands back /var/..., git reports the
  # /private/var/... it resolves to, and every path comparison below would fail.
  TMPDIR="$(cd "$(mktemp -d)" && pwd -P)"
  SEED="$TMPDIR/seed"
  CONTAINER="$TMPDIR/container"
  FAKE_BIN="$TMPDIR/bin"
  mkdir -p "$FAKE_BIN"
}

teardown() {
  rm -rf "$TMPDIR"
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

# _fake_claude [EXIT] — a `claude` on PATH that reports where it ran and with what.
_fake_claude() {
  local code="${1:-0}"
  cat > "$FAKE_BIN/claude" <<SCRIPT
#!/usr/bin/env bash
printf 'LAUNCHED_IN=%s\n' "\$(pwd -P)"
printf 'ARGS=%s\n' "\$*"
exit $code
SCRIPT
  chmod +x "$FAKE_BIN/claude"
}

# _launch DIR [ARGS...] — source the snippet in DIR and call the wrapper there,
# then report the shell's own directory so a leaked `cd` is visible.
_launch() {
  local dir="$1"
  shift
  PATH="$FAKE_BIN:$REPO_ROOT/bin:$PATH" run zsh -c "
    cd '$dir'
    source '$SNIPPET'
    claude $*
    print -- \"WRAPPER_RC=\$?\"
    print -- \"SHELL_STAYED_IN=\$(pwd -P)\"
  "
}

# ── Redirected ───────────────────────────────────────────────────────────────

@test "launches in the worktree when started at a bare container" {
  _make_seed
  _make_container
  _fake_claude

  _launch "$CONTAINER"
  [ "$status" -eq 0 ]
  [[ "$output" == *"LAUNCHED_IN=$CONTAINER/main"* ]]
}

@test "says where it redirected to" {
  _make_seed
  _make_container
  _fake_claude

  _launch "$CONTAINER"
  [[ "$output" == *"is a bare repository — launching in $CONTAINER/main"* ]]
}

@test "leaves the calling shell in the container" {
  _make_seed
  _make_container
  _fake_claude

  _launch "$CONTAINER"
  [[ "$output" == *"SHELL_STAYED_IN=$CONTAINER"* ]]
  [[ "$output" != *"SHELL_STAYED_IN=$CONTAINER/main"* ]]
}

@test "forwards arguments through the redirect" {
  _make_seed
  _make_container
  _fake_claude

  _launch "$CONTAINER" --resume "'a b'"
  [[ "$output" == *"ARGS=--resume a b"* ]]
}

@test "returns the redirected session's exit status" {
  _make_seed
  _make_container
  _fake_claude 3

  _launch "$CONTAINER"
  [[ "$output" == *"WRAPPER_RC=3"* ]]
}

# ── Passed through ───────────────────────────────────────────────────────────

@test "launches in place inside a worktree" {
  _make_seed
  _make_container
  _fake_claude

  _launch "$CONTAINER/main"
  [[ "$output" == *"LAUNCHED_IN=$CONTAINER/main"* ]]
  [[ "$output" != *"launching in"* ]]
}

@test "launches in place in an ordinary repo" {
  _make_seed
  _fake_claude

  _launch "$SEED"
  [[ "$output" == *"LAUNCHED_IN=$SEED"* ]]
  [[ "$output" != *"launching in"* ]]
}

@test "launches in place outside any repo" {
  local loose="$TMPDIR/loose"
  mkdir -p "$loose"
  _fake_claude

  _launch "$loose"
  [[ "$output" == *"LAUNCHED_IN=$loose"* ]]
  [[ "$output" != *"launching in"* ]]
}

@test "passes through when resolve-worktree is not installed" {
  _make_seed
  _make_container
  _fake_claude

  # A PATH with no workbench bin dir on it — not even the installed one, which
  # would otherwise resolve and defeat the case.
  PATH="$FAKE_BIN:/usr/bin:/bin" run zsh -c "
    cd '$CONTAINER'
    source '$SNIPPET'
    claude
  "
  [ "$status" -eq 0 ]
  [[ "$output" == *"LAUNCHED_IN=$CONTAINER"* ]]
}

# ── Nothing to redirect to ───────────────────────────────────────────────────

@test "reports a container with no worktree instead of redirecting silently" {
  _make_seed
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
  _fake_claude

  _launch "$CONTAINER"
  [[ "$output" == *"no worktree on 'main'"* ]]
  [[ "$output" == *"cannot resolve a worktree for $CONTAINER"* ]]
  [[ "$output" == *"LAUNCHED_IN=$CONTAINER"* ]]
}
