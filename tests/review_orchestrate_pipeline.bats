#!/usr/bin/env bats

setup_file() {
  load 'test_helper'
  # warm .pyc cache; errors caught at import
  python3 -m compileall -q "$REPO_ROOT/ai/claude/lib" "$REPO_ROOT/ai/claude/bin" 2>/dev/null || true
  export ORCHESTRATE="$REPO_ROOT/ai/claude/bin/review-orchestrate"
}

setup() {
  load 'test_helper'
  load 'review_orchestrate_helper'
  common_setup
  TMPDIR="$BATS_TEST_TMPDIR"
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

# ── Diagnostics ─────────────────────────────────────────────────────────────

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

# ── Model error detection ────────────────────────────────────────────────────

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

# ── Review recovery ──────────────────────────────────────────────────────────

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

# ── Pipeline resume / recovery ───────────────────────────────────────────────

@test "_resolve_recovery: returns fresh state when no pipeline file exists" {
  result=$(_py_here <<'PYEOF'
import json
from dataclasses import dataclass, field

job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=mod.PRMetadata(
        title="t", body="", head="b", base="main", head_sha="abc",
        additions=10, deletions=5, changed_files=2, files=[]),
    ctx=mod.PRContext(), wt_path="/tmp", review_file="$TMPDIR/nonexistent.md",
    session_log="/tmp/log.jsonl",
)
groups = [mod.Group("g1", ["a.go"], 10)]
plan = mod._resolve_recovery(job, groups)
print(plan.cost_so_far, plan.skip_groups, plan.skip_holistic, plan.state)
PYEOF
)
  [ "$result" = "0.0 None False None" ]
}

@test "_resolve_recovery: auto-resumes when valid incomplete pipeline state exists" {
  mkdir -p "$TMPDIR/test"
  cat > "$TMPDIR/test/pipeline.json" <<'EOF'
{"head_sha": "abc123", "group_names": ["g1", "g2"], "holistic_done": true, "groups_done": [1]}
EOF
  result=$(_py_here <<PYEOF
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=mod.PRMetadata(
        title="t", body="", head="b", base="main", head_sha="abc123",
        additions=10, deletions=5, changed_files=2, files=[]),
    ctx=mod.PRContext(), wt_path="/tmp", review_file="$TMPDIR/test/review.md",
    session_log="/tmp/log.jsonl",
)
groups = [mod.Group("g1", ["a.go"], 10), mod.Group("g2", ["b.go"], 20)]
plan = mod._resolve_recovery(job, groups)
print(plan.skip_groups, plan.skip_holistic, plan.state is not None)
PYEOF
)
  # _info prints a status line to stdout; check last line for the actual result
  last_line=$(echo "$result" | tail -1)
  [ "$last_line" = "{1} True True" ]
}

@test "_resolve_recovery: starts fresh when SHA differs" {
  mkdir -p "$TMPDIR/stale"
  cat > "$TMPDIR/stale/pipeline.json" <<'EOF'
{"head_sha": "old_sha", "group_names": ["g1"], "holistic_done": true, "groups_done": [1]}
EOF
  result=$(_py_here <<PYEOF
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=mod.PRMetadata(
        title="t", body="", head="b", base="main", head_sha="new_sha",
        additions=10, deletions=5, changed_files=2, files=[]),
    ctx=mod.PRContext(), wt_path="/tmp", review_file="$TMPDIR/stale/review.md",
    session_log="/tmp/log.jsonl",
)
groups = [mod.Group("g1", ["a.go"], 10)]
plan = mod._resolve_recovery(job, groups)
print(plan.state)
PYEOF
)
  last_line=$(echo "$result" | tail -1)
  [ "$last_line" = "None" ]
  # Stale pipeline state should be deleted so it doesn't block fresh runs
  [ ! -f "$TMPDIR/stale/pipeline.json" ]
}

@test "_resolve_recovery: completed run with failed groups returns retry set" {
  _py_here <<'PY'
import json, tempfile
from pathlib import Path

d = tempfile.mkdtemp()
review_file = f"{d}/review.md"
Path(review_file).write_text("## Summary\nMechanical fallback\n## Verdict\nApprove")

state_data = {
    "head_sha": "abc123",
    "group_names": ["tier1-critical", "orc-card", "svc-card"],
    "holistic_done": True,
    "groups_done": [1, 3],
    "groups_failed": {"2": "agent error: model not available"},
    "synthesis_done": True,
    "synthesis_failed": "mechanical fallback (no output)",
}
Path(f"{d}/pipeline.json").write_text(json.dumps(state_data))

groups = [
    mod.Group("tier1-critical", ["a.go"], 100),
    mod.Group("orc-card", ["b.go"], 200),
    mod.Group("svc-card", ["c.go"], 150),
]

job = mod.ReviewJob(
    repo="org/repo", pr_number="42",
    pr=mod.PRMetadata("t","b","h","base","abc123",10,5,3,[]),
    ctx=mod.PRContext(), wt_path=d, review_file=review_file,
    session_log=f"{d}/session.jsonl",
)

plan = mod._resolve_recovery(job, groups)
cost, skip_groups, skip_holistic, state = (
    plan.cost_so_far, plan.skip_groups, plan.skip_holistic, plan.state,
)
assert skip_groups == {1, 3}, f"expected skip {{1, 3}}, got {skip_groups}"
assert skip_holistic is True
assert state is not None
assert state.synthesis_done is False, "synthesis must be re-run after patching"
PY
}

@test "_resolve_recovery: completed run with no failures returns done signal" {
  _py_here <<'PY'
import json, tempfile
from pathlib import Path

d = tempfile.mkdtemp()
review_file = f"{d}/review.md"
Path(review_file).write_text("## Summary\nGood review\n## Verdict\nApprove")

state_data = {
    "head_sha": "abc123",
    "group_names": ["tier1-critical"],
    "holistic_done": True,
    "groups_done": [1],
    "groups_failed": {},
    "synthesis_done": True,
    "synthesis_failed": "",
}
Path(f"{d}/pipeline.json").write_text(json.dumps(state_data))

groups = [mod.Group("tier1-critical", ["a.go"], 100)]

job = mod.ReviewJob(
    repo="org/repo", pr_number="42",
    pr=mod.PRMetadata("t","b","h","base","abc123",10,5,1,[]),
    ctx=mod.PRContext(), wt_path=d, review_file=review_file,
    session_log=f"{d}/session.jsonl",
)

plan = mod._resolve_recovery(job, groups)
cost, skip_groups, skip_holistic, state = (
    plan.cost_so_far, plan.skip_groups, plan.skip_holistic, plan.state,
)
assert state is None, "state should be None when review is complete with no failures"
PY
}

@test "_resolve_recovery: synthesis-only failure retries synthesis" {
  _py_here <<'PY'
import json, tempfile
from pathlib import Path

d = tempfile.mkdtemp()
review_file = f"{d}/review.md"
Path(review_file).write_text("## Summary\nmechanically merged\n## Verdict\nApprove (mechanically merged)")

state_data = {
    "head_sha": "abc123",
    "group_names": ["tier1-critical", "orc-card"],
    "holistic_done": True,
    "groups_done": [1, 2],
    "groups_failed": {},
    "synthesis_done": True,
    "synthesis_failed": "mechanical fallback (no output)",
}
Path(f"{d}/pipeline.json").write_text(json.dumps(state_data))

groups = [
    mod.Group("tier1-critical", ["a.go"], 100),
    mod.Group("orc-card", ["b.go"], 200),
]

job = mod.ReviewJob(
    repo="org/repo", pr_number="42",
    pr=mod.PRMetadata("t","b","h","base","abc123",10,5,2,[]),
    ctx=mod.PRContext(), wt_path=d, review_file=review_file,
    session_log=f"{d}/session.jsonl",
)

plan = mod._resolve_recovery(job, groups)
cost, skip_groups, skip_holistic, state = (
    plan.cost_so_far, plan.skip_groups, plan.skip_holistic, plan.state,
)
assert skip_groups == {1, 2}, f"expected skip {{1, 2}}, got {skip_groups}"
assert skip_holistic is True
assert state is not None
assert state.synthesis_done is False, "synthesis must be re-run"
PY
}

@test "_resolve_recovery: incomplete pipeline resumes from where it left off" {
  _py_here <<'PY'
import json, tempfile
from pathlib import Path

d = tempfile.mkdtemp()
review_file = f"{d}/review.md"

state_data = {
    "head_sha": "abc123",
    "group_names": ["tier1-critical", "orc-card", "svc-card"],
    "holistic_done": True,
    "groups_done": [1],
    "groups_failed": {},
    "synthesis_done": False,
    "synthesis_failed": "",
}
Path(f"{d}/pipeline.json").write_text(json.dumps(state_data))

groups = [
    mod.Group("tier1-critical", ["a.go"], 100),
    mod.Group("orc-card", ["b.go"], 200),
    mod.Group("svc-card", ["c.go"], 150),
]

job = mod.ReviewJob(
    repo="org/repo", pr_number="42",
    pr=mod.PRMetadata("t","b","h","base","abc123",10,5,3,[]),
    ctx=mod.PRContext(), wt_path=d, review_file=review_file,
    session_log=f"{d}/session.jsonl",
)

plan = mod._resolve_recovery(job, groups)
cost, skip_groups, skip_holistic, state = (
    plan.cost_so_far, plan.skip_groups, plan.skip_holistic, plan.state,
)
assert skip_groups == {1}, f"expected skip {{1}}, got {skip_groups}"
assert skip_holistic is True
assert state is not None
PY
}


# ── Pipeline state (resume/retry) ───────────────────────────────────────────

@test "PipelineState: write/read round-trip preserves all fields" {
  result=$(_py "
import json, io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(
        head_sha='abc123',
        group_names=['tier1', 'services', 'tests'],
        holistic_done=True,
        groups_done=[1, 3],
    )
review_file = '$TMPDIR/review.md'
job = mod.ReviewJob(
    repo='org/repo', pr_number='1',
    pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc123',
        additions=10, deletions=5, changed_files=1, files=[]),
    ctx=mod.PRContext(), wt_path='/tmp/wt', review_file=review_file,
    session_log='/tmp/s.jsonl',
)
mod._write_pipeline_state(job, state)
loaded = mod._read_pipeline_state(job)
print(f'sha={loaded.head_sha}')
print(f'count={loaded.group_count}')
print(f'names={loaded.group_names}')
print(f'holistic={loaded.holistic_done}')
print(f'groups={loaded.groups_done}')
")
  echo "$result"
  [[ "$result" == *"sha=abc123"* ]]
  [[ "$result" == *"count=3"* ]]
  [[ "$result" == *"names=['tier1', 'services', 'tests']"* ]]
  [[ "$result" == *"holistic=True"* ]]
  [[ "$result" == *"groups=[1, 3]"* ]]
}

@test "_read_pipeline_state: missing file returns None" {
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/nonexistent-review.md',
        session_log='/tmp/s.jsonl',
    )
    result = mod._read_pipeline_state(job)
print(result)
")
  [ "$result" = "None" ]
}

@test "_read_pipeline_state: corrupt JSON returns None" {
  echo "not valid json" > "$TMPDIR/pipeline.json"
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='/tmp/s.jsonl',
    )
    result = mod._read_pipeline_state(job)
print(result)
")
  [ "$result" = "None" ]
}

@test "_sum_existing_costs: sums costs from log files" {
  # Create fake JSONL log files with cost data
  echo '{"type": "result", "total_cost_usd": 1.50}' > "$TMPDIR/holistic.jsonl"
  echo '{"type": "result", "total_cost_usd": 0.75}' > "$TMPDIR/group-1.jsonl"
  echo '{"type": "result", "total_cost_usd": 0.50}' > "$TMPDIR/group-2.jsonl"

  result=$(_sum_costs "group_names=['a', 'b', 'c'], holistic_done=True, groups_done=[1, 2]")
  [ "$result" = "2.75" ]
}

@test "_sum_existing_costs: missing log files return 0" {
  result=$(_sum_costs "group_names=['a', 'b'], holistic_done=True, groups_done=[1]")
  [ "$result" = "0.00" ]
}

@test "_sum_existing_costs: counts the scout log when phase 1 scouted" {
  # holistic_done means "phase 1 finished" — the scout branch sets it too, and
  # writes scout.jsonl rather than holistic.jsonl.
  echo '{"type": "result", "total_cost_usd": 1.25}' > "$TMPDIR/scout.jsonl"

  result=$(_sum_costs "group_names=['a'], holistic_done=True")
  [ "$result" = "1.25" ]
}

@test "_sum_existing_costs: counts both phase-1 logs when effort changed" {
  # Resume only validates the head SHA and the group names, so a run that
  # holisticked and resumed at an effort that scouts leaves both logs behind.
  echo '{"type": "result", "total_cost_usd": 1.50}' > "$TMPDIR/holistic.jsonl"
  echo '{"type": "result", "total_cost_usd": 1.25}' > "$TMPDIR/scout.jsonl"

  result=$(_sum_costs "group_names=['a'], holistic_done=True")
  [ "$result" = "2.75" ]
}

@test "_sum_existing_costs: counts a group that crashed before it was marked done" {
  echo '{"type": "result", "total_cost_usd": 0.75}' > "$TMPDIR/group-2.jsonl"

  result=$(_sum_costs "group_names=['a', 'b'], groups_done=[1]")
  [ "$result" = "0.75" ]
}

@test "_sum_existing_costs: counts the synthesis and disprove logs" {
  # Recovery from a synthesis failure sums costs before clearing the flag, so a
  # costly synthesis attempt has to survive into the resumed run's budget.
  echo '{"type": "result", "total_cost_usd": 2.00}' > "$TMPDIR/synthesis.jsonl"
  echo '{"type": "result", "total_cost_usd": 0.50}' > "$TMPDIR/disprove.jsonl"

  result=$(_sum_costs "group_names=['a']")
  [ "$result" = "2.50" ]
}

@test "FILENAME_PIPELINE_STATE constant exists" {
  result=$(_py "print(mod.FILENAME_PIPELINE_STATE)")
  [ "$result" = "pipeline.json" ]
}

@test "_derive_path: produces folder-relative paths" {
  _py_here <<'PY'
result = mod._derive_path("/reviews/maximum-1206/review.md", "group-1.md")
assert result == "/reviews/maximum-1206/group-1.md", f"got {result}"
PY
}

@test "_derive_path: works for all intermediate types" {
  _py_here <<'PY'
base = "/reviews/maximum-1206/review.md"
assert mod._derive_path(base, "pipeline.json") == "/reviews/maximum-1206/pipeline.json"
assert mod._derive_path(base, "holistic.md") == "/reviews/maximum-1206/holistic.md"
assert mod._derive_path(base, "holistic.jsonl") == "/reviews/maximum-1206/holistic.jsonl"
assert mod._derive_path(base, "group-3.md") == "/reviews/maximum-1206/group-3.md"
assert mod._derive_path(base, "group-3.jsonl") == "/reviews/maximum-1206/group-3.jsonl"
assert mod._derive_path(base, "synthesis.jsonl") == "/reviews/maximum-1206/synthesis.jsonl"
assert mod._derive_path(base, "session.jsonl") == "/reviews/maximum-1206/session.jsonl"
assert mod._derive_path(base, "meta.json") == "/reviews/maximum-1206/meta.json"
assert mod._derive_path(base, "prior.md") == "/reviews/maximum-1206/prior.md"
PY
}

@test "--resume flag removed from CLI (auto-resume is default)" {
  run "$ORCHESTRATE" --help
  [[ "$output" != *"--resume"* ]]
}

@test "_consolidate_logs: merges log files without deleting intermediates" {
  echo '{"type":"result","total_cost_usd":1.0}' > "$TMPDIR/holistic.jsonl"
  echo '{"type":"result","total_cost_usd":0.5}' > "$TMPDIR/group-1.jsonl"
  echo '{"type":"result","total_cost_usd":0.3}' > "$TMPDIR/synthesis.jsonl"
  echo "holistic content" > "$TMPDIR/holistic.md"
  echo "group content" > "$TMPDIR/group-1.md"

  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/session.jsonl',
    )
    mod._consolidate_logs(
        job,
        holistic_log='$TMPDIR/holistic.jsonl',
        group_count=1,
        synthesis_log='$TMPDIR/synthesis.jsonl',
    )
import os
session_exists = os.path.exists('$TMPDIR/session.jsonl')
holistic_exists = os.path.exists('$TMPDIR/holistic.md')
group_exists = os.path.exists('$TMPDIR/group-1.md')
holistic_log_exists = os.path.exists('$TMPDIR/holistic.jsonl')
print(f'session={session_exists},holistic={holistic_exists},group={group_exists},hlog={holistic_log_exists}')
")
  echo "$result"
  [ "$result" = "session=True,holistic=True,group=True,hlog=True" ]
}

@test "_cleanup_intermediates: removes every phase artifact and pipeline state" {
  # Regression for #675: disprove.md and disprove.jsonl outlived the pass
  # because the call site enumerated what to remove and never named them.
  echo "scout" > "$TMPDIR/scout.md"
  echo "slog" > "$TMPDIR/scout.jsonl"
  echo "holistic" > "$TMPDIR/holistic.md"
  echo "log" > "$TMPDIR/holistic.jsonl"
  echo "group" > "$TMPDIR/group-1.md"
  echo "glog" > "$TMPDIR/group-1.jsonl"
  echo "glog2" > "$TMPDIR/group-2.jsonl"
  echo "synth" > "$TMPDIR/synthesis.jsonl"
  echo "disprove" > "$TMPDIR/disprove.md"
  echo "dlog" > "$TMPDIR/disprove.jsonl"
  echo '{}' > "$TMPDIR/pipeline.json"

  result=$(_py "
import io, contextlib, os
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/session.jsonl',
    )
    mod._cleanup_intermediates(job)
remaining = []
for f in ['scout.md', 'scout.jsonl', 'holistic.md', 'holistic.jsonl',
          'group-1.md', 'group-1.jsonl', 'group-2.jsonl', 'synthesis.jsonl',
          'disprove.md', 'disprove.jsonl', 'pipeline.json']:
    if os.path.exists('$TMPDIR/' + f):
        remaining.append(f)
print(f'remaining={remaining}')
")
  echo "$result"
  [ "$result" = "remaining=[]" ]
}

@test "_cleanup_intermediates: sweeps a prior --fix pass's log too" {
  # fix.jsonl is diagnostic, not a finding, so a re-review's cleanup sweeps
  # it the same as any other phase log rather than letting it survive.
  echo "review" > "$TMPDIR/review.md"
  echo "flog" > "$TMPDIR/fix.jsonl"

  result=$(_py "
import io, contextlib, os
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/session.jsonl',
    )
    mod._cleanup_intermediates(job)
print(f'fix_exists={os.path.exists(\"$TMPDIR/fix.jsonl\")}')
")
  [ "$result" = "fix_exists=False" ]
}

@test "_cleanup_intermediates: preserves the deliverable and its sidecars" {
  echo "review" > "$TMPDIR/review.md"
  echo '{"type":"result"}' > "$TMPDIR/session.jsonl"
  echo '{}' > "$TMPDIR/meta.json"
  echo "prior" > "$TMPDIR/prior.md"
  echo "holistic" > "$TMPDIR/holistic.md"

  result=$(_py "
import io, contextlib, os
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/session.jsonl',
    )
    mod._cleanup_intermediates(job)
kept = [f for f in ['review.md', 'session.jsonl', 'meta.json', 'prior.md']
        if os.path.exists('$TMPDIR/' + f)]
print(f'kept={kept},holistic={os.path.exists(\"$TMPDIR/holistic.md\")}')
")
  echo "$result"
  [ "$result" = "kept=['review.md', 'session.jsonl', 'meta.json', 'prior.md'],holistic=False" ]
}

@test "_cleanup_intermediates: preserves prompt-stats.json" {
  echo '[]' > "$TMPDIR/prompt-stats.json"
  echo "prompt" > "$TMPDIR/prompt-self-review.md"

  result=$(_py "
import io, contextlib, os
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/session.jsonl',
    )
    mod._cleanup_intermediates(job)
stats_exists = os.path.exists('$TMPDIR/prompt-stats.json')
prompt_exists = os.path.exists('$TMPDIR/prompt-self-review.md')
print(f'stats={stats_exists},prompt={prompt_exists}')
")
  [ "$result" = "stats=True,prompt=False" ]
}

@test "_review_group: recovery skip returns early when output exists" {
  echo "existing group review" > "$TMPDIR/group-1.md"

  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=10, deletions=5, changed_files=1,
            files=[{'path': 'a.go', 'additions': 10, 'deletions': 5}]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/s.jsonl',
    )
    grp = mod.Group(name='services', files=['a.go'], lines=15)
    idx, output, failed = mod._review_group(
        1, grp, job, 3, 'holistic', skip=mod.GroupSkip.RECOVERY,
    )
print(f'idx={idx},failed={failed}')
import os
print(f'output_exists={os.path.exists(output)}')
")
  echo "$result"
  [[ "$result" == *"idx=1,failed=None"* ]]
  [[ "$result" == *"output_exists=True"* ]]
}

@test "_review_group: recovery skip with missing output reports failure" {
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=10, deletions=5, changed_files=1,
            files=[{'path': 'a.go', 'additions': 10, 'deletions': 5}]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/s.jsonl',
    )
    grp = mod.Group(name='services', files=['a.go'], lines=15)
    idx, output, failed = mod._review_group(
        1, grp, job, 3, 'holistic', skip=mod.GroupSkip.RECOVERY,
    )
print(f'idx={idx},group={failed.group},reason={failed.diagnosis.message}')
")
  echo "$result"
  [[ "$result" == *"idx=1,group=services,reason=output missing"* ]]
}

@test "_review_group: carried skip with no output is not a failure" {
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=10, deletions=5, changed_files=1,
            files=[{'path': 'a.go', 'additions': 10, 'deletions': 5}]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/s.jsonl',
    )
    grp = mod.Group(name='services', files=['a.go'], lines=15)
    idx, output, failed = mod._review_group(
        1, grp, job, 3, 'holistic', skip=mod.GroupSkip.CARRIED,
    )
import os
print(f'idx={idx},failed={failed},output_exists={os.path.exists(output)}')
")
  echo "$result"
  [[ "$result" == *"idx=1,failed=None,output_exists=False"* ]]
}

@test "_build_group_skips: keeps incremental and recovery skips distinct" {
  _py_here <<'PY'
skips = mod._build_group_skips({1, 6}, None)
assert skips == {1: mod.GroupSkip.CARRIED, 6: mod.GroupSkip.CARRIED}, skips

skips = mod._build_group_skips(set(), {2, 3})
assert skips == {2: mod.GroupSkip.RECOVERY, 3: mod.GroupSkip.RECOVERY}, skips

# A group both carried and already on disk is a recovery skip: its output
# exists, so reusing it beats re-deriving findings from the prior review.
skips = mod._build_group_skips({1, 6}, {1, 2})
assert skips == {
    1: mod.GroupSkip.RECOVERY,
    2: mod.GroupSkip.RECOVERY,
    6: mod.GroupSkip.CARRIED,
}, skips
PY
}

@test "_validate_resume_state: matching state returns valid" {
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(
        head_sha='abc123',
        group_names=['services', 'tests'],
        holistic_done=True, groups_done=[1],
    )
    groups = [mod.Group('services', ['a.go'], 10), mod.Group('tests', ['b_test.go'], 5)]
    valid = mod._validate_resume_state(state, 'abc123', groups)
print(valid)
")
  [ "$result" = "True" ]
}

@test "_validate_resume_state: stale SHA returns invalid" {
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(
        head_sha='old_sha',
        group_names=['services', 'tests'],
    )
    groups = [mod.Group('services', ['a.go'], 10), mod.Group('tests', ['b_test.go'], 5)]
    valid = mod._validate_resume_state(state, 'new_sha', groups)
print(valid)
")
  [ "$result" = "False" ]
}

@test "_validate_resume_state: group name mismatch returns invalid" {
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(
        head_sha='abc',
        group_names=['services', 'tests'],
    )
    groups = [mod.Group('services', ['a.go'], 10), mod.Group('infra', ['c.go'], 5)]
    valid = mod._validate_resume_state(state, 'abc', groups)
print(valid)
")
  [ "$result" = "False" ]
}

@test "_update_group_done: thread-safe state update" {
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(
        head_sha='abc',
        group_names=['a', 'b', 'c'],
        groups_done=[1],
    )
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='/tmp/s.jsonl',
    )
    mod._update_group_done(job, 3, state)
    mod._update_group_done(job, 2, state)
    mod._update_group_done(job, 1, state)  # duplicate, should not add
    loaded = mod._read_pipeline_state(job)
print(f'groups={loaded.groups_done}')
")
  [ "$result" = "groups=[1, 2, 3]" ]
}

@test "PipelineState: round-trips groups_failed through JSON" {
  _py_here <<'PY'
import json, tempfile
from pathlib import Path

state = mod.PipelineState(
    head_sha="abc123",
    group_names=["tier1-critical", "orc-card"],
    holistic_done=True,
    groups_done=[1],
    groups_failed={2: mod.Diagnosis(
        mod.DiagnosisKind.AGENT_ERROR, detail="model not available",
    )},
    synthesis_done=False,
    synthesis_failed=mod.Diagnosis(
        mod.DiagnosisKind.UNKNOWN, detail="agent exited with code 1 (no output)",
    ),
)

d = tempfile.mkdtemp()
review_file = f"{d}/review.md"
Path(review_file).write_text("")

job = mod.ReviewJob(
    repo="org/repo", pr_number="42",
    pr=mod.PRMetadata("t","b","h","base","abc123",10,5,2,[]),
    ctx=mod.PRContext(), wt_path=d, review_file=review_file,
    session_log=f"{d}/session.jsonl",
)

mod._write_pipeline_state(job, state)
loaded = mod._read_pipeline_state(job)
assert loaded.groups_failed == {2: mod.Diagnosis(
    mod.DiagnosisKind.AGENT_ERROR, detail="model not available",
)}, f"got {loaded.groups_failed}"
assert loaded.synthesis_done is False
assert loaded.synthesis_failed == mod.Diagnosis(
    mod.DiagnosisKind.UNKNOWN, detail="agent exited with code 1 (no output)",
), f"got {loaded.synthesis_failed}"
PY
}

@test "PipelineState: missing new fields default gracefully" {
  _py_here <<'PY'
import json, tempfile
from pathlib import Path

d = tempfile.mkdtemp()
review_file = f"{d}/review.md"
Path(review_file).write_text("")

# Write a legacy pipeline state without the new fields
state_file = f"{d}/pipeline.json"
Path(state_file).write_text(json.dumps({
    "head_sha": "abc123",
    "group_names": ["tier1-critical"],
    "holistic_done": True,
    "groups_done": [1],
}))

job = mod.ReviewJob(
    repo="org/repo", pr_number="42",
    pr=mod.PRMetadata("t","b","h","base","abc123",10,5,2,[]),
    ctx=mod.PRContext(), wt_path=d, review_file=review_file,
    session_log=f"{d}/session.jsonl",
)

loaded = mod._read_pipeline_state(job)
assert loaded.groups_failed == {}, f"got {loaded.groups_failed}"
assert loaded.synthesis_done is False, f"got {loaded.synthesis_done}"
assert loaded.synthesis_failed is None, f"got {loaded.synthesis_failed}"
PY
}

@test "_update_group_failed: records failure reason in pipeline state" {
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(
        head_sha='abc',
        group_names=['a', 'b', 'c'],
        groups_done=[1],
    )
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='/tmp/s.jsonl',
    )
    mod._update_group_failed(job, 2, mod.Diagnosis(mod.DiagnosisKind.MAX_TURNS, num_turns=10), state)
    mod._update_group_failed(job, 3, mod.Diagnosis(mod.DiagnosisKind.AGENT_ERROR, detail='model not available'), state)
    loaded = mod._read_pipeline_state(job)
reasons = {i: d.message for i, d in loaded.groups_failed.items()}
print(f'failed={reasons}')
print(f'done={loaded.groups_done}')
")
  [[ "$result" == *"failed={2: 'agent hit max turns (10)', 3: 'agent error: model not available'}"* ]]
  [[ "$result" == *"done=[1]"* ]]
}

@test "invoke_agent: returns subprocess exit code" {
  result=$(_py "
import subprocess, ai_backend_claude as abc, ai_backend as ab
original = abc._build_agent_cmd
abc._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'echo fail >&2; exit 42']
rc = mod.invoke_agent(ab.AgentInvocation(prompt='test', cwd='$TMPDIR', session_log='$TMPDIR/test.jsonl', add_dirs=['/tmp', '/tmp']))
abc._build_agent_cmd = original
print(rc)
")
  [ "$result" = "42" ]
}

@test "invoke_agent: logs stderr on failure" {
  result=$(_py "
import subprocess, os, ai_backend_claude as abc, ai_backend as ab
original = abc._build_agent_cmd
abc._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'echo agent-error-msg >&2; exit 1']
mod.invoke_agent(ab.AgentInvocation(prompt='test', cwd='$TMPDIR', session_log='$TMPDIR/stderr_test.jsonl', add_dirs=['/tmp', '/tmp']))
abc._build_agent_cmd = original
content = open('$TMPDIR/stderr_test.jsonl').read()
print('has_stderr=' + str('agent-error-msg' in content))
")
  [ "$result" = "has_stderr=True" ]
}

@test "invoke_agent: tolerates subprocess that exits before reading stdin" {
  result=$(_py "
import ai_backend_claude as abc, ai_backend as ab
original = abc._build_agent_cmd
abc._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'exit 7']
rc = mod.invoke_agent(ab.AgentInvocation(prompt='a]long prompt that the subprocess never reads', cwd='$TMPDIR', session_log='$TMPDIR/pipe_test.jsonl', add_dirs=['/tmp', '/tmp']))
abc._build_agent_cmd = original
print(rc)
")
  [ "$result" = "7" ]
}

@test "invoke_fix: tolerates subprocess that exits before reading stdin" {
  result=$(_py "
import ai_backend_claude as abc, ai_backend as ab
original = abc._build_fix_cmd
abc._build_fix_cmd = lambda *a, **kw: ['bash', '-c', 'exit 13']
rc = abc.invoke_fix(ab.AgentInvocation(prompt='a long prompt that the subprocess never reads', cwd='$TMPDIR', add_dirs=['/tmp']))
abc._build_fix_cmd = original
print(rc)
")
  [ "$result" = "13" ]
}

@test "PipelineState: rejects group_count as constructor arg" {
  result=$(_py "
try:
    state = mod.PipelineState(head_sha='abc', group_count=2, group_names=['a', 'b'])
    print('accepted')
except TypeError:
    print('rejected')
")
  [ "$result" = "rejected" ]
}

