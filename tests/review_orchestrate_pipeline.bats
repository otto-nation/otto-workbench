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

@test "_check_budget: sums costs and detects exceeded" {
  cat > "$TMPDIR/log1.jsonl" <<'EOF'
{"type":"result","subtype":"success","total_cost_usd":5.00}
EOF
  cat > "$TMPDIR/log2.jsonl" <<'EOF'
{"type":"result","subtype":"success","total_cost_usd":8.00}
EOF
  result=$(_py "
total, exceeded = mod._check_budget(['$TMPDIR/log1.jsonl', '$TMPDIR/log2.jsonl'], 10.0)
print(f'{total:.2f} {exceeded}')
")
  [ "$result" = "13.00 True" ]
}

@test "_check_budget: under budget returns False" {
  cat > "$TMPDIR/log.jsonl" <<'EOF'
{"type":"result","subtype":"success","total_cost_usd":2.00}
EOF
  result=$(_py "
total, exceeded = mod._check_budget(['$TMPDIR/log.jsonl'], 10.0)
print(f'{total:.2f} {exceeded}')
")
  [ "$result" = "2.00 False" ]
}

# ── Diagnostics ─────────────────────────────────────────────────────────────

@test "_diagnose_result_type: handles error_max_turns subtype" {
  result=$(_py '
r = {"type": "result", "subtype": "error_max_turns", "is_error": True,
     "num_turns": 10, "errors": ["Reached maximum number of turns (10)"]}
print(mod._diagnose_result_type(r))
')
  [ "$result" = "agent hit max turns (10)" ]
}

@test "_diagnose_result_type: handles plain max_turns subtype" {
  result=$(_py '
r = {"type": "result", "subtype": "max_turns", "num_turns": 5}
print(mod._diagnose_result_type(r))
')
  [ "$result" = "agent hit max turns (5)" ]
}

@test "_diagnose_result_type: extracts error from errors list" {
  result=$(_py '
r = {"type": "result", "subtype": "error", "is_error": True,
     "errors": ["Connection refused"]}
print(mod._diagnose_result_type(r))
')
  [ "$result" = "agent error: Connection refused" ]
}

@test "_diagnose_result_type: falls back to error key" {
  result=$(_py '
r = {"type": "result", "subtype": "error", "is_error": True,
     "error": "timeout"}
print(mod._diagnose_result_type(r))
')
  [ "$result" = "agent error: timeout" ]
}

@test "_diagnose_result_type: unknown error when no error info" {
  result=$(_py '
r = {"type": "result", "subtype": "error", "is_error": True}
print(mod._diagnose_result_type(r))
')
  [ "$result" = "agent error: unknown" ]
}

@test "_diagnose_result_type: extracts error from result field when errors list empty" {
  result=$(_py '
r = {"type": "result", "subtype": "success", "is_error": True,
     "api_error_status": 404, "errors": [],
     "result": "The model claude-sonnet-4-5 is not available on your vertex deployment."}
print(mod._diagnose_result_type(r))
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

@test "_try_recover_review: recovers review from denied Bash heredoc write" {
  cat > "$TMPDIR/session.jsonl" <<'EOF'
{"type":"result","is_error":true,"permission_denials":[{"tool_name":"Bash","tool_input":{"command":"cat > /tmp/review.md << 'REVIEW_EOF'\n## Summary\nNo issues found.\n\n## Verdict\nApprove\nREVIEW_EOF"}}]}
EOF
  _py_here <<PYEOF
job = mod.ReviewJob(
    repo='org/repo', pr_number='1',
    pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
        additions=1, deletions=0, changed_files=1, files=[]),
    ctx=mod.PRContext(), wt_path='/tmp/wt',
    review_file='$TMPDIR/recovered.md',
    session_log='$TMPDIR/session.jsonl', reviews_dir='/tmp/reviews',
)
mod._try_recover_review(job)
PYEOF
  [ -f "$TMPDIR/recovered.md" ]
  grep -q "## Summary" "$TMPDIR/recovered.md"
  grep -q "## Verdict" "$TMPDIR/recovered.md"
}

@test "_try_recover_review: recovers review from denied Write tool" {
  python3 -c "
import json
record = {'type': 'result', 'is_error': True, 'permission_denials': [
    {'tool_name': 'Write', 'tool_input': {'file_path': '/tmp/review.md', 'content': '## Summary\nClean review.\n\n## Verdict\nApprove\n'}}
]}
print(json.dumps(record))
" > "$TMPDIR/session2.jsonl"
  _py_here <<PYEOF
job = mod.ReviewJob(
    repo='org/repo', pr_number='1',
    pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
        additions=1, deletions=0, changed_files=1, files=[]),
    ctx=mod.PRContext(), wt_path='/tmp/wt',
    review_file='$TMPDIR/recovered2.md',
    session_log='$TMPDIR/session2.jsonl', reviews_dir='/tmp/reviews',
)
mod._try_recover_review(job)
PYEOF
  [ -f "$TMPDIR/recovered2.md" ]
  grep -q "## Summary" "$TMPDIR/recovered2.md"
}

@test "_try_recover_review: no-op when session log missing" {
  _py_here <<PYEOF
job = mod.ReviewJob(
    repo='org/repo', pr_number='1',
    pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
        additions=1, deletions=0, changed_files=1, files=[]),
    ctx=mod.PRContext(), wt_path='/tmp/wt',
    review_file='$TMPDIR/should_not_exist.md',
    session_log='$TMPDIR/nonexistent.jsonl', reviews_dir='/tmp/reviews',
)
mod._try_recover_review(job)
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
    session_log="/tmp/log.jsonl", reviews_dir="/tmp/reviews",
)
groups = [mod.Group("g1", ["a.go"], 10)]
cost, skip_groups, skip_hol, state = mod._resolve_recovery(job, groups)
print(cost, skip_groups, skip_hol, state)
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
    session_log="/tmp/log.jsonl", reviews_dir="/tmp/reviews",
)
groups = [mod.Group("g1", ["a.go"], 10), mod.Group("g2", ["b.go"], 20)]
cost, skip_groups, skip_hol, state = mod._resolve_recovery(job, groups)
print(skip_groups, skip_hol, state is not None)
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
    session_log="/tmp/log.jsonl", reviews_dir="/tmp/reviews",
)
groups = [mod.Group("g1", ["a.go"], 10)]
cost, skip_groups, skip_hol, state = mod._resolve_recovery(job, groups)
print(state)
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
    session_log=f"{d}/session.jsonl", reviews_dir=d,
)

cost, skip_groups, skip_holistic, state = mod._resolve_recovery(job, groups)
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
    session_log=f"{d}/session.jsonl", reviews_dir=d,
)

cost, skip_groups, skip_holistic, state = mod._resolve_recovery(job, groups)
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
    session_log=f"{d}/session.jsonl", reviews_dir=d,
)

cost, skip_groups, skip_holistic, state = mod._resolve_recovery(job, groups)
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
    session_log=f"{d}/session.jsonl", reviews_dir=d,
)

cost, skip_groups, skip_holistic, state = mod._resolve_recovery(job, groups)
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
    session_log='/tmp/s.jsonl', reviews_dir='/tmp/reviews',
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
        session_log='/tmp/s.jsonl', reviews_dir='/tmp/reviews',
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
        session_log='/tmp/s.jsonl', reviews_dir='/tmp/reviews',
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

  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(
        head_sha='abc',
        group_names=['a', 'b', 'c'],
        holistic_done=True,
        groups_done=[1, 2],
    )
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='/tmp/s.jsonl', reviews_dir='/tmp/reviews',
    )
    cost = mod._sum_existing_costs(job, state)
print(f'{cost:.2f}')
")
  [ "$result" = "2.75" ]
}

@test "_sum_existing_costs: missing log files return 0" {
  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(
        head_sha='abc',
        group_names=['a', 'b'],
        holistic_done=True,
        groups_done=[1],
    )
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='/tmp/s.jsonl', reviews_dir='/tmp/reviews',
    )
    cost = mod._sum_existing_costs(job, state)
print(f'{cost:.2f}')
")
  [ "$result" = "0.00" ]
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
        reviews_dir='/tmp/reviews',
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

@test "_cleanup_intermediates: removes intermediate files and pipeline state" {
  echo "holistic" > "$TMPDIR/holistic.md"
  echo "log" > "$TMPDIR/holistic.jsonl"
  echo "group" > "$TMPDIR/group-1.md"
  echo "glog" > "$TMPDIR/group-1.jsonl"
  echo "glog2" > "$TMPDIR/group-2.jsonl"
  echo "synth" > "$TMPDIR/synthesis.jsonl"
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
        reviews_dir='/tmp/reviews',
    )
    mod._cleanup_intermediates(
        job,
        holistic_output='$TMPDIR/holistic.md',
        holistic_log='$TMPDIR/holistic.jsonl',
        group_outputs=['$TMPDIR/group-1.md'],
        group_count=2,
        synthesis_log='$TMPDIR/synthesis.jsonl',
    )
remaining = []
for f in ['holistic.md', 'holistic.jsonl', 'group-1.md',
          'group-1.jsonl', 'synthesis.jsonl', 'pipeline.json']:
    if os.path.exists('$TMPDIR/' + f):
        remaining.append(f)
print(f'remaining={remaining}')
")
  echo "$result"
  [ "$result" = "remaining=[]" ]
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
        reviews_dir='/tmp/reviews',
    )
    mod._cleanup_intermediates(
        job,
        holistic_output='', holistic_log='',
        group_outputs=[], group_count=0,
        synthesis_log='',
    )
stats_exists = os.path.exists('$TMPDIR/prompt-stats.json')
prompt_exists = os.path.exists('$TMPDIR/prompt-self-review.md')
print(f'stats={stats_exists},prompt={prompt_exists}')
")
  [ "$result" = "stats=True,prompt=False" ]
}

@test "_review_group: skip=True returns early when output exists" {
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
        session_log='$TMPDIR/s.jsonl', reviews_dir='/tmp/reviews',
    )
    grp = mod.Group(name='services', files=['a.go'], lines=15)
    idx, output, failed = mod._review_group(1, grp, job, 3, 'holistic', skip=True)
print(f'idx={idx},failed={failed}')
import os
print(f'output_exists={os.path.exists(output)}')
")
  echo "$result"
  [[ "$result" == *"idx=1,failed=None"* ]]
  [[ "$result" == *"output_exists=True"* ]]
}

@test "_review_group: skip=True with missing output reports failure" {
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
        session_log='$TMPDIR/s.jsonl', reviews_dir='/tmp/reviews',
    )
    grp = mod.Group(name='services', files=['a.go'], lines=15)
    idx, output, failed = mod._review_group(1, grp, job, 3, 'holistic', skip=True)
print(f'idx={idx},failed={failed}')
")
  echo "$result"
  [[ "$result" == *"idx=1,failed=('services', 'output missing')"* ]]
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
        session_log='/tmp/s.jsonl', reviews_dir='/tmp/reviews',
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
    groups_failed={2: "agent error: model not available"},
    synthesis_done=False,
    synthesis_failed="agent exited with code 1 (no output)",
)

d = tempfile.mkdtemp()
review_file = f"{d}/review.md"
Path(review_file).write_text("")

job = mod.ReviewJob(
    repo="org/repo", pr_number="42",
    pr=mod.PRMetadata("t","b","h","base","abc123",10,5,2,[]),
    ctx=mod.PRContext(), wt_path=d, review_file=review_file,
    session_log=f"{d}/session.jsonl", reviews_dir=d,
)

mod._write_pipeline_state(job, state)
loaded = mod._read_pipeline_state(job)
assert loaded.groups_failed == {2: "agent error: model not available"}, f"got {loaded.groups_failed}"
assert loaded.synthesis_done is False
assert loaded.synthesis_failed == "agent exited with code 1 (no output)"
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
    session_log=f"{d}/session.jsonl", reviews_dir=d,
)

loaded = mod._read_pipeline_state(job)
assert loaded.groups_failed == {}, f"got {loaded.groups_failed}"
assert loaded.synthesis_done is False, f"got {loaded.synthesis_done}"
assert loaded.synthesis_failed == "", f"got {loaded.synthesis_failed}"
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
        session_log='/tmp/s.jsonl', reviews_dir='/tmp/reviews',
    )
    mod._update_group_failed(job, 2, 'agent hit max turns (10)', state)
    mod._update_group_failed(job, 3, 'agent error: model not available', state)
    loaded = mod._read_pipeline_state(job)
print(f'failed={loaded.groups_failed}')
print(f'done={loaded.groups_done}')
")
  [[ "$result" == *"failed={2: 'agent hit max turns (10)', 3: 'agent error: model not available'}"* ]]
  [[ "$result" == *"done=[1]"* ]]
}

@test "invoke_agent: returns subprocess exit code" {
  result=$(_py "
import subprocess, ai_backend_claude as abc
original = abc._build_agent_cmd
abc._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'echo fail >&2; exit 42']
rc = mod.invoke_agent('test', '$TMPDIR/test.jsonl', '/tmp', '/tmp')
abc._build_agent_cmd = original
print(rc)
")
  [ "$result" = "42" ]
}

@test "invoke_agent: logs stderr on failure" {
  result=$(_py "
import subprocess, os, ai_backend_claude as abc
original = abc._build_agent_cmd
abc._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'echo agent-error-msg >&2; exit 1']
mod.invoke_agent('test', '$TMPDIR/stderr_test.jsonl', '/tmp', '/tmp')
abc._build_agent_cmd = original
content = open('$TMPDIR/stderr_test.jsonl').read()
print('has_stderr=' + str('agent-error-msg' in content))
")
  [ "$result" = "has_stderr=True" ]
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

