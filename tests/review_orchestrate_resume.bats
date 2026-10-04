#!/usr/bin/env bats
# Tests for review-orchestrate: resuming a run — recovery resolution, resume validation, group skips.

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

@test "_resolve_recovery: returns fresh state when no pipeline file exists" {
  result=$(_py_here <<'PYEOF'
import json

job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=mod.PRMetadata(
        title="t", body="", head="b", base="main", head_sha="abc",
        additions=10, deletions=5, changed_files=2, files=[]),
    ctx=mod.PRContext(), wt_path="/tmp", review_file="$TMPDIR/nonexistent.md",
    session_log="/tmp/log.jsonl",
)
groups = [mod.Group("g1", ["a.go"], 10)]
plan = mod._resolve_recovery(job, groups)
print(plan.cost_so_far, plan.skip_groups, plan.state)
PYEOF
)
  [ "$result" = "0.0 None None" ]
}

@test "_resolve_recovery: auto-resumes when valid incomplete pipeline state exists" {
  mkdir -p "$TMPDIR/test"
  cat > "$TMPDIR/test/pipeline.json" <<'EOF'
{"head_sha": "abc123", "group_names": ["g1", "g2"], "done": ["holistic"], "groups_done": [1]}
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
print(plan.skip_groups, plan.state.scanned)
PYEOF
)
  # _info prints a status line to stdout; check last line for the actual result
  last_line=$(echo "$result" | tail -1)
  [ "$last_line" = "{1} True" ]
}

@test "_resolve_recovery: starts fresh when SHA differs" {
  mkdir -p "$TMPDIR/stale"
  cat > "$TMPDIR/stale/pipeline.json" <<'EOF'
{"head_sha": "old_sha", "group_names": ["g1"], "done": ["holistic"], "groups_done": [1]}
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
    "groups_done": [1, 3],
    "groups_failed": {"2": "agent error: model not available"},
    "done": ["holistic", "synthesis"],
    "failed": {"synthesis": "mechanical fallback (no output)"},
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
cost, skip_groups, state = plan.cost_so_far, plan.skip_groups, plan.state
assert skip_groups == {1, 3}, f"expected skip {{1, 3}}, got {skip_groups}"
assert state is not None
assert state.scanned is True
assert mod.Phase.SYNTHESIS not in state.done, "synthesis must be re-run after patching"
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
    "groups_done": [1],
    "groups_failed": {},
    "done": ["holistic", "synthesis", "disprove"],
    "failed": {},
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
assert plan.state is None, "state should be None when review is complete with no failures"
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
    "groups_done": [1, 2],
    "groups_failed": {},
    "done": ["holistic", "synthesis"],
    "failed": {"synthesis": "mechanical fallback (no output)"},
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
cost, skip_groups, state = plan.cost_so_far, plan.skip_groups, plan.state
assert skip_groups == {1, 2}, f"expected skip {{1, 2}}, got {skip_groups}"
assert state is not None
assert state.scanned is True
assert mod.Phase.SYNTHESIS not in state.done, "synthesis must be re-run"
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
    "groups_done": [1],
    "groups_failed": {},
    "done": ["holistic"],
    "failed": {},
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
cost, skip_groups, state = plan.cost_so_far, plan.skip_groups, plan.state
assert skip_groups == {1}, f"expected skip {{1}}, got {skip_groups}"
assert state is not None
assert state.scanned is True
PY
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
        done={mod.Phase.HOLISTIC}, groups_done=[1],
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
