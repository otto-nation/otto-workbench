#!/usr/bin/env bats
# Tests for review-orchestrate: pipeline state, cost sums, artifact paths, log consolidation, cleanup.

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

@test "PipelineState: write/read round-trip preserves all fields" {
  result=$(_py "
import json, io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    state = mod.PipelineState(
        head_sha='abc123',
        group_names=['tier1', 'services', 'tests'],
        done={mod.Phase.HOLISTIC},
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
print(f'done={sorted(str(p) for p in loaded.done)}')
print(f'groups={loaded.groups_done}')
")
  echo "$result"
  [[ "$result" == *"sha=abc123"* ]]
  [[ "$result" == *"count=3"* ]]
  [[ "$result" == *"names=['tier1', 'services', 'tests']"* ]]
  [[ "$result" == *"done=['holistic']"* ]]
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

  result=$(_sum_costs "group_names=['a', 'b', 'c'], groups_done=[1, 2]")
  [ "$result" = "2.75" ]
}

@test "_sum_existing_costs: missing log files return 0" {
  result=$(_sum_costs "group_names=['a', 'b'], groups_done=[1]")
  [ "$result" = "0.00" ]
}

@test "_sum_existing_costs: counts the scout log when phase 1 scouted" {
  # Phase 1 is either scan, and the one that ran is the one that left a log —
  # which is why the total is read from the logs rather than from `state.done`.
  echo '{"type": "result", "total_cost_usd": 1.25}' > "$TMPDIR/scout.jsonl"

  result=$(_sum_costs "group_names=['a']")
  [ "$result" = "1.25" ]
}

@test "_sum_existing_costs: counts both phase-1 logs when effort changed" {
  # Resume only validates the head SHA and the group names, so a run that
  # holisticked and resumed at an effort that scouts leaves both logs behind.
  echo '{"type": "result", "total_cost_usd": 1.50}' > "$TMPDIR/holistic.jsonl"
  echo '{"type": "result", "total_cost_usd": 1.25}' > "$TMPDIR/scout.jsonl"

  result=$(_sum_costs "group_names=['a']")
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

@test "review_artifact_path: produces folder-relative paths" {
  _py_here <<'PY'
result = mod.review_artifact_path("/reviews/maximum-1206/review.md", "group-1.md")
assert result == "/reviews/maximum-1206/group-1.md", f"got {result}"
PY
}

@test "review_artifact_path: works for all intermediate types" {
  _py_here <<'PY'
base = "/reviews/maximum-1206/review.md"
assert mod.review_artifact_path(base, "pipeline.json") == "/reviews/maximum-1206/pipeline.json"
assert mod.review_artifact_path(base, "holistic.md") == "/reviews/maximum-1206/holistic.md"
assert mod.review_artifact_path(base, "holistic.jsonl") == "/reviews/maximum-1206/holistic.jsonl"
assert mod.review_artifact_path(base, "group-3.md") == "/reviews/maximum-1206/group-3.md"
assert mod.review_artifact_path(base, "group-3.jsonl") == "/reviews/maximum-1206/group-3.jsonl"
assert mod.review_artifact_path(base, "synthesis.jsonl") == "/reviews/maximum-1206/synthesis.jsonl"
assert mod.review_artifact_path(base, "session.jsonl") == "/reviews/maximum-1206/session.jsonl"
assert mod.review_artifact_path(base, "meta.json") == "/reviews/maximum-1206/meta.json"
assert mod.review_artifact_path(base, "prior.md") == "/reviews/maximum-1206/prior.md"
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

@test "cleanup_intermediates: removes every phase artifact and pipeline state" {
  # Regression: disprove.md and disprove.jsonl outlived the pass
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
import os, pathlib
mod.cleanup_intermediates(pathlib.Path('$TMPDIR'))
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

@test "cleanup_intermediates: sweeps the --fix pass's log too" {
  # fix.jsonl is diagnostic, not a finding, so the sweep takes it the same as
  # any other phase log rather than letting it survive the run.
  echo "review" > "$TMPDIR/review.md"
  echo "flog" > "$TMPDIR/fix.jsonl"

  result=$(_py "
import os, pathlib
mod.cleanup_intermediates(pathlib.Path('$TMPDIR'))
print(f'fix_exists={os.path.exists(\"$TMPDIR/fix.jsonl\")}')
")
  [ "$result" = "fix_exists=False" ]
}

@test "cleanup_intermediates: sweeps the --fix pass's tracking file too" {
  # The checklist the agent answers on is named rather than derived: it belongs
  # to the fix engine, not the phase registry, so the glob never reaches it.
  echo "review" > "$TMPDIR/review.md"
  echo "## <!-- fix:M1 -->" > "$TMPDIR/fix-tracking.md"

  result=$(_py "
import os, pathlib
mod.cleanup_intermediates(pathlib.Path('$TMPDIR'))
print(f'tracking_exists={os.path.exists(\"$TMPDIR/fix-tracking.md\")}')
")
  [ "$result" = "tracking_exists=False" ]
}

@test "cleanup_intermediates: sweeps every verify-tracking chunk" {
  echo "review" > "$TMPDIR/review.md"
  echo "chunk1" > "$TMPDIR/verify-tracking-1.md"
  echo "chunk2" > "$TMPDIR/verify-tracking-2.md"
  echo "legacy" > "$TMPDIR/verify-tracking.md"

  result=$(_py "
import os, pathlib
mod.cleanup_intermediates(pathlib.Path('$TMPDIR'))
print(os.path.exists('$TMPDIR/verify-tracking-1.md'),
      os.path.exists('$TMPDIR/verify-tracking-2.md'),
      os.path.exists('$TMPDIR/verify-tracking.md'))
")
  [ "$result" = "False False False" ]
}

@test "cleanup_intermediates: preserves the deliverable and its sidecars" {
  echo "review" > "$TMPDIR/review.md"
  echo '{"type":"result"}' > "$TMPDIR/session.jsonl"
  echo '{}' > "$TMPDIR/meta.json"
  echo "prior" > "$TMPDIR/prior.md"
  echo "holistic" > "$TMPDIR/holistic.md"

  result=$(_py "
import os, pathlib
mod.cleanup_intermediates(pathlib.Path('$TMPDIR'))
kept = [f for f in ['review.md', 'session.jsonl', 'meta.json', 'prior.md']
        if os.path.exists('$TMPDIR/' + f)]
print(f'kept={kept},holistic={os.path.exists(\"$TMPDIR/holistic.md\")}')
")
  echo "$result"
  [ "$result" = "kept=['review.md', 'session.jsonl', 'meta.json', 'prior.md'],holistic=False" ]
}

@test "cleanup_intermediates: preserves prompt-stats.json" {
  echo '[]' > "$TMPDIR/prompt-stats.json"
  echo "prompt" > "$TMPDIR/prompt-self-review.md"

  result=$(_py "
import os, pathlib
mod.cleanup_intermediates(pathlib.Path('$TMPDIR'))
stats_exists = os.path.exists('$TMPDIR/prompt-stats.json')
prompt_exists = os.path.exists('$TMPDIR/prompt-self-review.md')
print(f'stats={stats_exists},prompt={prompt_exists}')
")
  [ "$result" = "stats=True,prompt=False" ]
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

@test "PipelineState: round-trips the failure maps through JSON" {
  _py_here <<'PY'
import json, tempfile
from pathlib import Path

state = mod.PipelineState(
    head_sha="abc123",
    group_names=["tier1-critical", "orc-card"],
    done={mod.Phase.HOLISTIC},
    groups_done=[1],
    groups_failed={2: mod.Diagnosis(
        mod.DiagnosisKind.AGENT_ERROR, detail="model not available",
    )},
    failed={mod.Phase.SYNTHESIS: mod.Diagnosis(
        mod.DiagnosisKind.UNKNOWN, detail="agent exited with code 1 (no output)",
    )},
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
assert loaded.done == {mod.Phase.HOLISTIC}, f"got {loaded.done}"
assert loaded.failed == {mod.Phase.SYNTHESIS: mod.Diagnosis(
    mod.DiagnosisKind.UNKNOWN, detail="agent exited with code 1 (no output)",
)}, f"got {loaded.failed}"
PY
}

@test "PipelineState: missing new fields default gracefully" {
  _py_here <<'PY'
import json, tempfile
from pathlib import Path

d = tempfile.mkdtemp()
review_file = f"{d}/review.md"
Path(review_file).write_text("")

# Write a legacy pipeline state without the new fields. `holistic_done` is one
# of the per-phase flags `done` replaced — an unknown key now, and ignored.
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
assert loaded.done == set(), f"got {loaded.done}"
assert loaded.failed == {}, f"got {loaded.failed}"
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
