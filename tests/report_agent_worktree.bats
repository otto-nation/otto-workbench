#!/usr/bin/env bats
# Tests for ai/bin/report-agent-worktree — the bash hook both harnesses run
# after a tool call, which starts Python only when the payload might name a
# worktree other than the agent's own, and always exits 0.
#
# A fake `hc` records every argv it is given, and a fake `python3` earlier on
# PATH records that it was started before handing over to the real one, so a
# case can assert both "no interpreter was started" and what was labelled.
# The hook is run through a symlink, the way ~/.local/bin installs it.

setup() {
  load 'test_helper'
  common_setup
  sandbox_state_dir

  local real_python
  real_python=$(command -v python3)

  FAKE_BIN="$TMPDIR/fake-bin"
  mkdir -p "$FAKE_BIN"
  export HC_LOG="$TMPDIR/hc.log"
  export PY_LOG="$TMPDIR/python.log"

  cat > "$FAKE_BIN/hc" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$HC_LOG"
EOF
  cat > "$FAKE_BIN/python3" <<EOF
#!/usr/bin/env bash
printf '%s\n' "\$*" >> "\$PY_LOG"
exec "$real_python" "\$@"
EOF
  chmod +x "$FAKE_BIN/hc" "$FAKE_BIN/python3"
  export PATH="$FAKE_BIN:$PATH"

  INSTALL_BIN="$TMPDIR/local-bin"
  mkdir -p "$INSTALL_BIN"
  ln -s "$REPO_ROOT/ai/bin/report-agent-worktree" "$INSTALL_BIN/report-agent-worktree"
  HOOK="$INSTALL_BIN/report-agent-worktree"

  export HC_TERMINAL_ID="t-1"
  export HC_SCOPE="s"
  export HC_TERMINAL_SOCKET="$TMPDIR/hc.sock"
  export CLAUDE_PROJECT_DIR="$TMPDIR/project"
  mkdir -p "$CLAUDE_PROJECT_DIR"
}

teardown() {
  common_teardown
}

# _hook PAYLOAD [ARGS...] — run the hook with PAYLOAD on stdin.
_hook() {
  local payload="$1"
  shift
  run "$HOOK" "$@" <<<"$payload"
}

# _container — a bare-repo container at $TMPDIR/c with worktrees main and
# feat-a; prints the `git worktree list` spelling of feat-a.
_container() {
  local seed="$TMPDIR/seed" root="$TMPDIR/c"
  git init -q -b main "$seed"
  git -C "$seed" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  mkdir -p "$root"
  git clone -q --bare "$seed" "$root/.git"
  git -C "$root/.git" worktree add -q "$root/main" main
  git -C "$root/.git" worktree add -q -b feat/a "$root/feat-a"
  git -C "$root/.git" worktree list --porcelain | sed -n 's/^worktree \(.*feat-a\)$/\1/p'
}

@test "no HC_TERMINAL_ID: exits 0 without starting python" {
  unset HC_TERMINAL_ID
  _hook '{"tool_name":"Edit","tool_input":{"file_path":"/elsewhere/f"}}'
  [ "$status" -eq 0 ]
  [ ! -e "$PY_LOG" ]
  [ ! -e "$HC_LOG" ]
}

@test "an empty HC_TERMINAL_ID counts as unset" {
  HC_TERMINAL_ID="" _hook '{"tool_name":"Edit","tool_input":{"file_path":"/elsewhere/f"}}'
  [ "$status" -eq 0 ]
  [ ! -e "$PY_LOG" ]
}

@test "a Bash payload naming nothing starts no python" {
  _hook "{\"tool_name\":\"Bash\",\"tool_input\":{\"command\":\"ls -la\"},\"cwd\":\"$CLAUDE_PROJECT_DIR\"}"
  [ "$status" -eq 0 ]
  [ ! -e "$PY_LOG" ]
}

@test "an Edit under CLAUDE_PROJECT_DIR starts no python" {
  _hook "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$CLAUDE_PROJECT_DIR/a.txt\"},\"cwd\":\"$CLAUDE_PROJECT_DIR\"}"
  [ "$status" -eq 0 ]
  [ ! -e "$PY_LOG" ]
}

@test "an Edit into another worktree labels the terminal with its path" {
  local feat
  feat=$(_container)
  [ -n "$feat" ]
  export CLAUDE_PROJECT_DIR="$TMPDIR/c/main"

  _hook "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$TMPDIR/c/feat-a/new.txt\"},\"cwd\":\"$CLAUDE_PROJECT_DIR\"}"

  [ "$status" -eq 0 ]
  [ -s "$PY_LOG" ]
  [ "$(cat "$HC_LOG")" = "label t-1 otto.worktree=$feat" ]
}

@test "--clear empties the label" {
  run "$HOOK" --clear </dev/null
  [ "$status" -eq 0 ]
  [ "$(cat "$HC_LOG")" = "label t-1 otto.worktree=" ]
}

@test "malformed JSON past the prefilter exits 0 and labels nothing" {
  _hook '{"tool_name":"Edit","tool_input":{"file_path": '
  [ "$status" -eq 0 ]
  [ -s "$PY_LOG" ]
  [ ! -e "$HC_LOG" ]
}

@test "a broken agent-worktree still leaves the hook exiting 0" {
  local copy="$TMPDIR/broken-bin"
  mkdir -p "$copy"
  cp "$REPO_ROOT/ai/bin/report-agent-worktree" "$copy/report-agent-worktree"
  printf 'import sys\nsys.exit(1)\n' > "$copy/agent-worktree"
  rm "$HOOK"
  ln -s "$copy/report-agent-worktree" "$HOOK"

  _hook '{"tool_name":"Edit","tool_input":{"file_path":"/elsewhere/f"}}'

  [ "$status" -eq 0 ]
  grep -q "$copy/agent-worktree report" "$PY_LOG"
}

@test "pi path: no CLAUDE_PROJECT_DIR still starts python for a file_path edit" {
  unset CLAUDE_PROJECT_DIR
  _hook '{"tool_name":"Edit","tool_input":{"file_path":"/tmp/some dir/x/f.txt"},"cwd":"/tmp"}'
  [ "$status" -eq 0 ]
  [ -s "$PY_LOG" ]
}

@test "file_path with a space inside the project starts no python" {
  mkdir -p "$CLAUDE_PROJECT_DIR/a b"
  _hook "{\"tool_name\":\"Edit\",\"tool_input\":{\"file_path\":\"$CLAUDE_PROJECT_DIR/a b/f.txt\"}}"
  [ "$status" -eq 0 ]
  [ ! -e "$PY_LOG" ]
}
