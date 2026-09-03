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
  TMPDIR="$BATS_TEST_TMPDIR"
}

teardown() {
  common_teardown
}

# ── _phase_merge ─────────────────────────────────────────────────────────────

@test "_phase_merge: renders failure reasons in review gaps" {
  result=$(_py_here <<'PYEOF'
result = mod._phase_merge([], [
    mod.GroupFailure("orc-card", mod.Diagnosis(
        mod.DiagnosisKind.AGENT_ERROR, detail="model not available")),
    mod.GroupFailure("svc-card", mod.Diagnosis(
        mod.DiagnosisKind.MAX_TURNS, num_turns=10)),
])
print(result)
PYEOF
)
  [[ "$result" == *"- orc-card: agent error: model not available"* ]]
  [[ "$result" == *"- svc-card: agent hit max turns (10)"* ]]
}

@test "_validate_group_output: valid output with sections returns True" {
  cat > "$TMPDIR/valid.md" <<'EOF'
## Must fix
- **[M1]** a.go:10 — bug
EOF
  result=$(_py "
print(mod._validate_group_output('$TMPDIR/valid.md', 'test-group'))
")
  [ "$result" = "True" ]
}

@test "_validate_group_output: no sections warns and returns False" {
  cat > "$TMPDIR/nosections.md" <<'EOF'
I looked at all the files and found nothing notable.
The code looks good overall.
EOF
  result=$(_py "
print(mod._validate_group_output('$TMPDIR/nosections.md', 'test-group'))
")
  [ "$result" = "False" ]
}

@test "_validate_group_output: empty file returns True" {
  : > "$TMPDIR/empty.md"
  result=$(_py "
print(mod._validate_group_output('$TMPDIR/empty.md', 'test-group'))
")
  [ "$result" = "True" ]
}

@test "_validate_group_output: File Triage section counts as valid" {
  cat > "$TMPDIR/triage.md" <<'EOF'
## File Triage
- `a.go` — Tier 2
EOF
  result=$(_py "
print(mod._validate_group_output('$TMPDIR/triage.md', 'triage-group'))
")
  [ "$result" = "True" ]
}


# ── _scope_prior_review ──────────────────────────────────────────────────────

@test "_scope_prior_review: keeps only findings for matching files" {
  prior='## Must fix
- [ ] **[M1]** src/auth.go:10 — auth bug
- [ ] **[M2]** src/db.go:20 — db bug

## Should fix
- [ ] **[S1]** src/auth.go:30 — cleanup'

  result=$(_py "
prior = '''$prior'''
scoped = mod._scope_prior_review(prior, ['src/auth.go'])
print(scoped)
")
  [[ "$result" == *"[M1]"* ]]
  [[ "$result" != *"[M2]"* ]]
  [[ "$result" == *"[S1]"* ]]
  [[ "$result" == *"## Must fix"* ]]
  [[ "$result" == *"## Should fix"* ]]
}

@test "_scope_prior_review: no matches returns empty" {
  prior='## Must fix
- [ ] **[M1]** src/auth.go:10 — auth bug'

  result=$(_py "
prior = '''$prior'''
scoped = mod._scope_prior_review(prior, ['src/unrelated.go'])
print(repr(scoped))
")
  [ "$result" = "''" ]
}

@test "_scope_prior_review: multiline finding continuation kept" {
  prior='## Must fix
- [ ] **[M1]** src/auth.go:10 — auth bug
  This is a continuation line with more detail
- [ ] **[M2]** src/db.go:20 — db bug'

  result=$(_py "
prior = '''$prior'''
scoped = mod._scope_prior_review(prior, ['src/auth.go'])
print(scoped)
")
  [[ "$result" == *"[M1]"* ]]
  [[ "$result" == *"continuation line"* ]]
  [[ "$result" != *"[M2]"* ]]
}

@test "build_prompt: GROUP template gets scoped prior review" {
  result=$(_py "
import types
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
    prior_review='## Must fix\n- [ ] **[M1]** src/auth.go:10 — auth bug\n- [ ] **[M2]** src/db.go:20 — db bug',
)
result = mod.build_prompt(mod.Phase.GROUP, job, max_turns=15,
    group_idx=1, group_count=2, group_name='auth',
    group_files_formatted='src/auth.go',
    holistic_content='', group_file_paths=['src/auth.go'],
)
print('[M1]' in result and '[M2]' not in result)
")
  [ "$result" = "True" ]
}

# ── open_counts ──────────────────────────────────────────────────────────────

@test "open_counts: counts by prefix" {
  result=$(_py "
text = '''## Must fix
- **[M1]** finding a
- **[M2]** finding b
## Should fix
- **[S1]** finding c
## Nit
- **[N1]** finding d
- **[N2]** finding e
- **[N3]** finding f
## Idioms
- **[I1]** finding g'''
counts = mod.ReviewDocument(body=text).open_counts
print(f\"M={counts['M']},S={counts['S']},N={counts['N']},I={counts['I']}\")
")
  [ "$result" = "M=2,S=1,N=3,I=1" ]
}
