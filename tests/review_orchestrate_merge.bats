#!/usr/bin/env bats

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

# ── _phase_merge ─────────────────────────────────────────────────────────────

@test "_phase_merge: renders failure reasons in review gaps" {
  result=$(_py_here <<'PYEOF'
from review.render import render_body
result = mod._phase_merge([], [
    mod.GroupFailure("orc-card", mod.Diagnosis(
        mod.DiagnosisKind.AGENT_ERROR, detail="model not available")),
    mod.GroupFailure("svc-card", mod.Diagnosis(
        mod.DiagnosisKind.MAX_TURNS, num_turns=10)),
], (), declinable=frozenset())
print(render_body(result.record))
PYEOF
)
  [[ "$result" == *"## Review gaps"* ]]
  [[ "$result" == *"- orc-card: agent error: model not available"* ]]
  [[ "$result" == *"- svc-card: agent hit max turns (10)"* ]]
}

# ── _scoped ──────────────────────────────────────────────────────────────────

@test "_scoped: keeps only findings for matching files" {
  result=$(_py_here <<'PYEOF'
from review.record import RecordFinding, ReviewRecord
from review.render import render_findings
prior = ReviewRecord(findings=(
    RecordFinding(id="M1", severity="M", path="src/auth.go", line=10, body="auth bug", checkbox=True),
    RecordFinding(id="M2", severity="M", path="src/db.go", line=20, body="db bug", checkbox=True),
    RecordFinding(id="S1", severity="S", path="src/auth.go", line=30, body="cleanup", checkbox=True),
))
scoped = mod._scoped(prior, ['src/auth.go'])
print(render_findings(scoped))
PYEOF
)
  [[ "$result" == *"[M1]"* ]]
  [[ "$result" != *"[M2]"* ]]
  [[ "$result" == *"[S1]"* ]]
  [[ "$result" == *"## Must fix"* ]]
  [[ "$result" == *"## Should fix"* ]]
}

@test "_scoped: no matches returns empty" {
  result=$(_py_here <<'PYEOF'
from review.record import RecordFinding, ReviewRecord
prior = ReviewRecord(findings=(
    RecordFinding(id="M1", severity="M", path="", body="auth bug", checkbox=True),
))
scoped = mod._scoped(prior, ['src/unrelated.go'])
print([f.id for f in scoped.findings])
PYEOF
)
  [ "$result" = "[]" ]
}

@test "_scoped: multiline finding continuation kept" {
  result=$(_py_here <<'PYEOF'
from review.record import RecordFinding, ReviewRecord
from review.render import render_findings
prior = ReviewRecord(findings=(
    RecordFinding(id="M1", severity="M", path="src/auth.go", line=10,
                 body="auth bug\n  This is a continuation line with more detail", checkbox=True),
    RecordFinding(id="M2", severity="M", path="src/db.go", line=20, body="db bug", checkbox=True),
))
scoped = mod._scoped(prior, ['src/auth.go'])
print(render_findings(scoped))
PYEOF
)
  [[ "$result" == *"[M1]"* ]]
  [[ "$result" == *"continuation line"* ]]
  [[ "$result" != *"[M2]"* ]]
}

@test "build_prompt: GROUP template gets scoped prior review" {
  result=$(_py_here <<'PYEOF'
from review.record import RecordFinding, ReviewRecord
from review.store import StoredReview
from review.types import ReviewMeta
pr = mod.PRMetadata(
    title='test', body='', base='main', head='feat', head_sha='abc123',
    additions=30, deletions=15, changed_files=2,
    files=[
        {'path': 'src/auth.go', 'additions': 10, 'deletions': 5, 'status': 'modified'},
        {'path': 'src/db.go', 'additions': 20, 'deletions': 10, 'status': 'modified'},
    ],
)
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo='org/repo', pr_number='1', pr=pr, ctx=ctx,
    wt_path='/tmp/wt', review_file='/tmp/review.md',
    session_log='/tmp/session.jsonl',
)
job.prior = StoredReview(
    ReviewRecord(findings=(
        RecordFinding(id="M1", severity="M", path="src/auth.go", line=10, body="auth bug", checkbox=True),
        RecordFinding(id="M2", severity="M", path="src/db.go", line=20, body="db bug", checkbox=True),
    )),
    ReviewMeta(),
)
result = mod.build_prompt(mod.Phase.GROUP, job, max_turns=15,
    group_idx=1, group_count=2, group_name='auth',
    group_files_formatted='src/auth.go',
    holistic_content='', group_file_paths=['src/auth.go'],
)
print('[M1]' in result and '[M2]' not in result)
PYEOF
)
  [ "$result" = "True" ]
}

# ── open_counts ──────────────────────────────────────────────────────────────

@test "open_counts: counts by prefix" {
  result=$(_py "
from review.record import RecordFinding, ReviewRecord
counts = ReviewRecord(findings=(
    RecordFinding(id='M1', severity='M', body='finding a'),
    RecordFinding(id='M2', severity='M', body='finding b'),
    RecordFinding(id='S1', severity='S', body='finding c'),
    RecordFinding(id='N1', severity='N', body='finding d'),
    RecordFinding(id='N2', severity='N', body='finding e'),
    RecordFinding(id='N3', severity='N', body='finding f'),
    RecordFinding(id='I1', severity='I', body='finding g'),
)).open_counts
print(f\"M={counts['M']},S={counts['S']},N={counts['N']},I={counts['I']}\")
")
  [ "$result" = "M=2,S=1,N=3,I=1" ]
}
