#!/usr/bin/env bats
# Tests for review-orchestrate: session cost, diagnostics, model-error detection, recovery, agent invocation.

setup_file() {
  load 'test_helper'
  # warm .pyc cache; errors caught at import
  python3 -m compileall -q "$REPO_ROOT/ai/lib" "$REPO_ROOT/ai/bin" 2>/dev/null || true
  export ORCHESTRATE="$REPO_ROOT/ai/bin/review-orchestrate"
}

setup() {
  load 'test_helper'
  load 'review_orchestrate_helper'
  common_setup
}

teardown() {
  common_teardown
}

@test "_parse_session_cost: extracts cost from JSONL" {
  cat > "$TMPDIR/cost.jsonl" <<'EOF'
{"type":"assistant","message":{"content":[{"type":"text","text":"working..."}]}}
{"type":"result","subtype":"success","is_error":false,"duration_ms":60000,"total_cost_usd":3.50,"usage":{"input_tokens":100,"output_tokens":200}}
EOF
  result=$(_py "
cost = mod._parse_session_cost('$TMPDIR/cost.jsonl')
print(f'{cost:.2f}')
")
  [ "$result" = "3.50" ]
}

@test "_parse_session_cost: returns 0 for missing file" {
  result=$(_py "
cost = mod._parse_session_cost('/tmp/nonexistent.jsonl')
print(f'{cost:.2f}')
")
  [ "$result" = "0.00" ]
}

@test "_diagnose_result_type: handles error_max_turns subtype" {
  result=$(_py '
r = {"type": "result", "subtype": "error_max_turns", "is_error": True,
     "num_turns": 10, "errors": ["Reached maximum number of turns (10)"]}
print(mod._diagnose_result_type(r).message)
')
  [ "$result" = "agent hit max turns (10)" ]
}

@test "_diagnose_result_type: handles plain max_turns subtype" {
  result=$(_py '
r = {"type": "result", "subtype": "max_turns", "num_turns": 5}
print(mod._diagnose_result_type(r).message)
')
  [ "$result" = "agent hit max turns (5)" ]
}

@test "_diagnose_result_type: extracts error from errors list" {
  result=$(_py '
r = {"type": "result", "subtype": "error", "is_error": True,
     "errors": ["Connection refused"]}
print(mod._diagnose_result_type(r).message)
')
  [ "$result" = "agent error: Connection refused" ]
}

@test "_diagnose_result_type: falls back to error key" {
  result=$(_py '
r = {"type": "result", "subtype": "error", "is_error": True,
     "error": "timeout"}
print(mod._diagnose_result_type(r).message)
')
  [ "$result" = "agent error: timeout" ]
}

@test "_diagnose_result_type: unknown error when no error info" {
  result=$(_py '
r = {"type": "result", "subtype": "error", "is_error": True}
print(mod._diagnose_result_type(r).message)
')
  [ "$result" = "agent error: unknown" ]
}

@test "_diagnose_result_type: extracts error from result field when errors list empty" {
  result=$(_py '
r = {"type": "result", "subtype": "success", "is_error": True,
     "api_error_status": 404, "errors": [],
     "result": "The model claude-sonnet-4-5 is not available on your vertex deployment."}
print(mod._diagnose_result_type(r).message)
')
  [ "$result" = "agent error: The model claude-sonnet-4-5 is not available on your vertex deployment." ]
}

@test "_is_model_error: detects 404 api_error_status" {
  echo '{"type":"result","api_error_status":404,"is_error":true,"result":"model not found"}' > "$TMPDIR/model404.jsonl"
  result=$(_py "print(mod._is_model_error('$TMPDIR/model404.jsonl'))")
  [ "$result" = "True" ]
}

@test "_is_model_error: detects 'not available' in result text" {
  echo '{"type":"result","is_error":true,"result":"The model claude-sonnet-4-5 is not available on your vertex deployment."}' > "$TMPDIR/notavail.jsonl"
  result=$(_py "print(mod._is_model_error('$TMPDIR/notavail.jsonl'))")
  [ "$result" = "True" ]
}

@test "_is_model_error: false for normal errors" {
  echo '{"type":"result","is_error":true,"errors":["Connection refused"]}' > "$TMPDIR/normal.jsonl"
  result=$(_py "print(mod._is_model_error('$TMPDIR/normal.jsonl'))")
  [ "$result" = "False" ]
}

@test "_is_model_error: false for missing log" {
  result=$(_py "print(mod._is_model_error('$TMPDIR/nonexistent.jsonl'))")
  [ "$result" = "False" ]
}

@test "try_recover_output: recovers review from denied Bash heredoc write" {
  cat > "$TMPDIR/session.jsonl" <<'EOF'
{"type":"result","is_error":true,"permission_denials":[{"tool_name":"Bash","tool_input":{"command":"cat > /tmp/review.md << 'REVIEW_EOF'\n## Summary\nNo issues found.\n\n## Verdict\nApprove\nREVIEW_EOF"}}]}
EOF
  _py_here <<PYEOF
mod.try_recover_output('$TMPDIR/session.jsonl', '$TMPDIR/recovered.md')
PYEOF
  [ -f "$TMPDIR/recovered.md" ]
  grep -q "## Summary" "$TMPDIR/recovered.md"
  grep -q "## Verdict" "$TMPDIR/recovered.md"
}

@test "try_recover_output: recovers review from denied Write tool" {
  python3 -c "
import json
record = {'type': 'result', 'is_error': True, 'permission_denials': [
    {'tool_name': 'Write', 'tool_input': {'file_path': '/tmp/review.md', 'content': '## Summary\nClean review.\n\n## Verdict\nApprove\n'}}
]}
print(json.dumps(record))
" > "$TMPDIR/session2.jsonl"
  _py_here <<PYEOF
mod.try_recover_output('$TMPDIR/session2.jsonl', '$TMPDIR/recovered2.md')
PYEOF
  [ -f "$TMPDIR/recovered2.md" ]
  grep -q "## Summary" "$TMPDIR/recovered2.md"
}

@test "try_recover_output: no-op when session log missing" {
  _py_here <<PYEOF
mod.try_recover_output('$TMPDIR/nonexistent.jsonl', '$TMPDIR/should_not_exist.md')
PYEOF
  [ ! -f "$TMPDIR/should_not_exist.md" ]
}

@test "invoke_agent: returns subprocess exit code" {
  result=$(_py "
import subprocess
from agent import backend_claude as abc
from agent import backend as ab
original = abc._build_agent_cmd
abc._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'echo fail >&2; exit 42']
rc = abc.invoke_agent(ab.AgentInvocation(prompt='test', cwd='$TMPDIR', session_log='$TMPDIR/test.jsonl', add_dirs=['/tmp', '/tmp']))
abc._build_agent_cmd = original
print(rc)
")
  [ "$result" = "42" ]
}

@test "invoke_agent: logs stderr on failure" {
  result=$(_py "
import subprocess, os
from agent import backend_claude as abc
from agent import backend as ab
original = abc._build_agent_cmd
abc._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'echo agent-error-msg >&2; exit 1']
abc.invoke_agent(ab.AgentInvocation(prompt='test', cwd='$TMPDIR', session_log='$TMPDIR/stderr_test.jsonl', add_dirs=['/tmp', '/tmp']))
abc._build_agent_cmd = original
content = open('$TMPDIR/stderr_test.jsonl').read()
print('has_stderr=' + str('agent-error-msg' in content))
")
  [ "$result" = "has_stderr=True" ]
}

@test "invoke_agent: tolerates subprocess that exits before reading stdin" {
  result=$(_py "
from agent import backend_claude as abc
from agent import backend as ab
original = abc._build_agent_cmd
abc._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'exit 7']
rc = abc.invoke_agent(ab.AgentInvocation(prompt='a]long prompt that the subprocess never reads', cwd='$TMPDIR', session_log='$TMPDIR/pipe_test.jsonl', add_dirs=['/tmp', '/tmp']))
abc._build_agent_cmd = original
print(rc)
")
  [ "$result" = "7" ]
}

@test "invoke_fix: tolerates subprocess that exits before reading stdin" {
  result=$(_py "
from agent import backend_claude as abc
from agent import backend as ab
original = abc._build_fix_cmd
abc._build_fix_cmd = lambda *a, **kw: ['bash', '-c', 'exit 13']
rc = abc.invoke_fix(ab.AgentInvocation(prompt='a long prompt that the subprocess never reads', cwd='$TMPDIR', add_dirs=['/tmp']))
abc._build_fix_cmd = original
print(rc)
")
  [ "$result" = "13" ]
}
