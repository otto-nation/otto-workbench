#!/usr/bin/env bats
# Tests for the agent-worktree Pi extension: detect.ts's payloadFor and
# shouldReport under plain node, and the cross-harness check that a Pi payload
# and Claude's payload for the same tool call label the terminal identically.
# select-on: ai/pi/extensions/agent-worktree/* ai/bin/agent-worktree ai/lib/pr/agent_worktree.py

setup() {
  load 'test_helper'
  common_setup
  sandbox_state_dir
  # One compile cache for the file: see tests/pi_extensions_issues.bats.
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  DETECT="$REPO_ROOT/ai/pi/extensions/agent-worktree/detect.ts"
}

teardown() {
  common_teardown
}

# _payload EVENT_JSON CWD — prints JSON.stringify(payloadFor(EVENT, CWD)).
_payload() {
  run node --input-type=module -e "
    const { payloadFor } = await import('$DETECT');
    process.stdout.write(JSON.stringify(payloadFor(JSON.parse(process.argv[1]), process.argv[2])));
  " -- "$1" "$2"
}

# _should MODE — prints shouldReport(MODE).
_should() {
  run node --input-type=module -e "
    const { shouldReport } = await import('$DETECT');
    process.stdout.write(String(shouldReport(process.argv[1])));
  " -- "$1"
}

@test "payloadFor: bash maps to Claude's Bash shape with the output as stdout" {
  _payload '{"toolName":"bash","input":{"command":"git -C ../b status"},"content":[{"type":"text","text":"clean"}]}' /w/a
  [ "$status" -eq 0 ]
  [ "$output" = '{"tool_name":"Bash","tool_input":{"command":"git -C ../b status"},"tool_response":{"stdout":"clean"},"cwd":"/w/a"}' ]
}

@test "payloadFor: edit and write map to file_path, a relative path resolved against cwd" {
  _payload '{"toolName":"edit","input":{"path":"../b/f.txt","oldText":"x","newText":"y"}}' /w/a
  [ "$status" -eq 0 ]
  [ "$output" = '{"tool_name":"Edit","tool_input":{"file_path":"/w/b/f.txt"},"cwd":"/w/a"}' ]

  _payload '{"toolName":"write","input":{"path":"/w/c/g.txt","content":"z"}}' /w/a
  [ "$status" -eq 0 ]
  [ "$output" = '{"tool_name":"Write","tool_input":{"file_path":"/w/c/g.txt"},"cwd":"/w/a"}' ]
}

@test "payloadFor: a tool naming no path is null" {
  _payload '{"toolName":"read","input":{"path":"/w/b/f.txt"}}' /w/a
  [ "$output" = null ]
  _payload '{"toolName":"bash","input":{}}' /w/a
  [ "$output" = null ]
}

@test "shouldReport: every interactive session, herdr or not" {
  _should tui t-1
  [ "$output" = true ]
  _should tui
  [ "$output" = true ]
  local mode
  for mode in json print rpc; do
    _should "$mode" t-1
    [ "$output" = false ] || { echo "$mode reported"; return 1; }
  done
}

@test "detect.ts imports node built-ins and _shared only" {
  run grep -nE "^import .* from \"[^n.]|@earendil-works" "$DETECT"
  [ "$status" -ne 0 ]
}

# ── cross-harness drift ──────────────────────────────────────────────────────
# One tool call, two harnesses' payloads, one parser. If Pi's reshaping drifts
# from what Claude sends, the Pi side labels nothing or a different path.

# _container — a bare-repo container at $TMPDIR/c with worktrees main and feat-a.
_container() {
  local seed="$TMPDIR/seed" root="$TMPDIR/c"
  git init -q -b main "$seed"
  git -C "$seed" -c user.email=t@t -c user.name=t commit -q --allow-empty -m init
  mkdir -p "$root"
  git clone -q --bare "$seed" "$root/.git"
  git -C "$root/.git" worktree add -q "$root/main" main
  git -C "$root/.git" worktree add -q -b feat/a "$root/feat-a"
}

# _report SIDE PAYLOAD [ENV...] — feeds PAYLOAD to `agent-worktree report`
# with a fake hc logging to $TMPDIR/SIDE.hc and a state root of its own, so
# one side's debounce cannot hide the other's label.
_report() {
  local side="$1" payload="$2"
  shift 2
  mkdir -p "$TMPDIR/fake-bin"
  cat > "$TMPDIR/fake-bin/hc" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$HC_LOG"
EOF
  chmod +x "$TMPDIR/fake-bin/hc"
  env -u CLAUDE_PROJECT_DIR PATH="$TMPDIR/fake-bin:$PATH" HC_TERMINAL_ID=t-1 \
    HC_LOG="$TMPDIR/$side.hc" WORKBENCH_STATE_DIR="$TMPDIR/state-$side" "$@" \
    python3 "$REPO_ROOT/ai/bin/agent-worktree" report <<<"$payload"
}

@test "drift: a Pi edit and a Claude Edit of the same file label the same worktree" {
  _container
  local home="$TMPDIR/c/main"
  _payload '{"toolName":"edit","input":{"path":"../feat-a/README.md","oldText":"a","newText":"b"}}' "$home"
  [ "$status" -eq 0 ]
  _report pi "$output"
  _report claude "$(jq -nc --arg f "$TMPDIR/c/feat-a/README.md" --arg h "$home" \
    '{session_id:"s",hook_event_name:"PostToolUse",tool_name:"Edit",
      tool_input:{file_path:$f,old_string:"a",new_string:"b"},
      tool_response:{filePath:$f},cwd:$h}')" CLAUDE_PROJECT_DIR="$home"

  [ -s "$TMPDIR/claude.hc" ]
  [[ "$(cat "$TMPDIR/claude.hc")" == "label t-1 otto.worktree="*"/feat-a" ]]
  [ "$(cat "$TMPDIR/pi.hc")" = "$(cat "$TMPDIR/claude.hc")" ]
}

@test "drift: a Pi bash and a Claude Bash of the same command label the same worktree" {
  _container
  local home="$TMPDIR/c/main"
  _payload '{"toolName":"bash","input":{"command":"git -C ../feat-a status"},"content":[{"type":"text","text":"ok"}]}' "$home"
  [ "$status" -eq 0 ]
  _report pi "$output"
  _report claude "$(jq -nc --arg h "$home" \
    '{session_id:"s",hook_event_name:"PostToolUse",tool_name:"Bash",
      tool_input:{command:"git -C ../feat-a status",description:"status"},
      tool_response:{stdout:"ok",stderr:"",interrupted:false},cwd:$h}')" CLAUDE_PROJECT_DIR="$home"

  [ -s "$TMPDIR/claude.hc" ]
  [[ "$(cat "$TMPDIR/claude.hc")" == "label t-1 otto.worktree="*"/feat-a" ]]
  [ "$(cat "$TMPDIR/pi.hc")" = "$(cat "$TMPDIR/claude.hc")" ]
}

@test "hookEnv carries the session only when claiming" {
  run node --input-type=module -e "
    const { hookEnv } = await import('$DETECT');
    process.stdout.write(JSON.stringify([
      hookEnv({}, '/w', {pid: 12, sessionId: 's'}),
      hookEnv({}, '/w', null),
    ]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[{"WORKBENCH_SESSION_HARNESS":"pi","CLAUDE_PROJECT_DIR":"/w","WORKBENCH_SESSION_PID":"12","PI_SESSION_ID":"s"},{"WORKBENCH_SESSION_HARNESS":"pi","CLAUDE_PROJECT_DIR":"/w"}]' ]
}

@test "hookEnv: the session cwd becomes CLAUDE_PROJECT_DIR; an empty cwd keeps it and still sets the harness" {
  run node --input-type=module -e "
    const { hookEnv } = await import('$DETECT');
    const env = { HOME: '/h', CLAUDE_PROJECT_DIR: '/old' };
    process.stdout.write(JSON.stringify([hookEnv(env, '/w/main'), hookEnv(env, ''), env]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[{"HOME":"/h","CLAUDE_PROJECT_DIR":"/w/main","WORKBENCH_SESSION_HARNESS":"pi"},{"HOME":"/h","CLAUDE_PROJECT_DIR":"/old","WORKBENCH_SESSION_HARNESS":"pi"},{"HOME":"/h","CLAUDE_PROJECT_DIR":"/old"}]' ]
}

@test "index.ts runs the hook with hookEnv's environment" {
  run grep -c 'const env = hookEnv(process.env, cwd, session);' "$REPO_ROOT/ai/pi/extensions/agent-worktree/index.ts"
  [ "$status" -eq 0 ]
  [ "$output" = "1" ]
  run grep -c 'timeout: 10_000, env }' "$REPO_ROOT/ai/pi/extensions/agent-worktree/index.ts"
  [ "$output" = "1" ]
}

@test "hookSession carries pid only when claiming" {
  run node --input-type=module -e "
    const { hookSession } = await import('$DETECT');
    process.stdout.write(JSON.stringify([
      hookSession({}, 12, 's'),
      hookSession({WORKBENCH_RUN_DIR: '/state/batch/r1/'}, 12, 'batch-r1'),
      hookSession({WORKBENCH_TARGET_DIR: '/t/dir', WORKBENCH_SESSION_ID: 'target-abc'}, 12, 'target-abc'),
      hookSession({WORKBENCH_RUN_DIR: '/state/batch/r1'}, 12, 'child-1'),
    ]));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '[{"pid":12,"sessionId":"s"},null,null,{"pid":12,"sessionId":"child-1"}]' ]
}

@test "index.ts gates both handlers through hookSession" {
  run grep -c 'hookSession(process.env, process.pid, sessionId)' "$REPO_ROOT/ai/pi/extensions/agent-worktree/index.ts"
  [ "$status" -eq 0 ]
  [ "$output" = "2" ]
}
