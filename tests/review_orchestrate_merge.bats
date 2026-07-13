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

@test "merge_reviews: merges sections and renumbers" {
  cat > "$TMPDIR/group1.md" <<'EOF'
## File Triage
- `a.go` — Tier 2

## Must fix
- **[M1]** a.go:10 — bug

## Nit
- **[N1]** a.go:20 — style
EOF

  cat > "$TMPDIR/group2.md" <<'EOF'
## File Triage
- `b.go` — Tier 2

## Must fix
- **[M1]** b.go:5 — other bug
EOF

  result=$(_py "
result = mod.merge_reviews(['$TMPDIR/group1.md', '$TMPDIR/group2.md'])
print(result)
")
  # First group's M1 stays M1, second group's M1 becomes M2
  [[ "$result" == *"[M1]"* ]]
  [[ "$result" == *"[M2]"* ]]
  [[ "$result" == *"a.go"* ]]
  [[ "$result" == *"b.go"* ]]
}

@test "merge_reviews: strips narrative paragraphs from file triage" {
  cat > "$TMPDIR/group1.md" <<'EOF'
## File Triage
- `a.go` — Tier 2

Both files are straightforward. No issues found.

### a.go

This file has a simple handler implementation.
EOF

  result=$(_py "
result = mod.merge_reviews(['$TMPDIR/group1.md'])
has_entry = '\`a.go\`' in result
has_narrative = 'Both files' in result
has_subsection = '### a.go' in result
print(f'entry={has_entry},narrative={has_narrative},subsection={has_subsection}')
")
  [ "$result" = "entry=True,narrative=False,subsection=False" ]
}

@test "merge_reviews: deduplicates triage entries across groups" {
  cat > "$TMPDIR/group1.md" <<'EOF'
## File Triage
- `shared.go` — Tier 1
- `a.go` — Tier 2
EOF

  cat > "$TMPDIR/group2.md" <<'EOF'
## File Triage
- `shared.go` — Tier 1
- `b.go` — Tier 2
EOF

  result=$(_py "
result = mod.merge_reviews(['$TMPDIR/group1.md', '$TMPDIR/group2.md'])
count = result.count('\`shared.go\`')
print(count)
")
  [ "$result" = "1" ]
}

@test "merge_reviews: strips --- separators from triage" {
  cat > "$TMPDIR/group1.md" <<'EOF'
## File Triage
- `a.go` — Tier 2
EOF

  cat > "$TMPDIR/group2.md" <<'EOF'
## File Triage
- `b.go` — Tier 2

---

Some paragraph about the files above.
EOF

  result=$(_py "
result = mod.merge_reviews(['$TMPDIR/group1.md', '$TMPDIR/group2.md'])
has_separator = '---' in result
print(f'separator={has_separator}')
")
  [ "$result" = "separator=False" ]
}

@test "merge_reviews: skips missing files" {
  cat > "$TMPDIR/exists.md" <<'EOF'
## File Triage
- `a.go` — Tier 2

## Should fix
- **[S1]** a.go:10 — cleanup
EOF

  result=$(_py "
result = mod.merge_reviews(['$TMPDIR/exists.md', '$TMPDIR/missing.md'])
print(result)
")
  [[ "$result" == *"[S1]"* ]]
  [[ "$result" == *"a.go"* ]]
}

@test "merge_reviews: deduplicates identical findings across groups" {
  cat > "$TMPDIR/group1.md" <<'EOF'
## File Triage
- `handler.go` — Tier 2

## Should fix
- **[S1]** **`handler.go:10`** — Missing error check on return value
EOF

  cat > "$TMPDIR/group2.md" <<'EOF'
## File Triage
- `handler.go` — Tier 2

## Should fix
- **[S1]** **`handler.go:10`** — Missing error check on return value
EOF

  result=$(_py "
result = mod.merge_reviews(['$TMPDIR/group1.md', '$TMPDIR/group2.md'])
count = result.count('Missing error check')
print(count)
")
  [ "$result" = "1" ]
}

@test "merge_reviews: keeps distinct findings for same file" {
  cat > "$TMPDIR/group1.md" <<'EOF'
## File Triage
- `handler.go` — Tier 2

## Nit
- **[N1]** **`handler.go:10`** — Use consistent naming style
EOF

  cat > "$TMPDIR/group2.md" <<'EOF'
## File Triage
- `handler.go` — Tier 2

## Nit
- **[N1]** **`handler.go:20`** — Add context to error wrapping
EOF

  result=$(_py "
result = mod.merge_reviews(['$TMPDIR/group1.md', '$TMPDIR/group2.md'])
has_naming = 'consistent naming' in result
has_error = 'error wrapping' in result
print(f'naming={has_naming},error={has_error}')
")
  [ "$result" = "naming=True,error=True" ]
}

@test "merge_reviews: renumbers after dedup" {
  cat > "$TMPDIR/group1.md" <<'EOF'
## File Triage
- `a.go` — Tier 2

## Must fix
- **[M1]** **`a.go:10`** — First unique finding
- **[M2]** **`a.go:20`** — Duplicate finding across groups
EOF

  cat > "$TMPDIR/group2.md" <<'EOF'
## File Triage
- `b.go` — Tier 2

## Must fix
- **[M1]** **`a.go:20`** — Duplicate finding across groups
- **[M2]** **`b.go:5`** — Second unique finding
EOF

  result=$(_py "
result = mod.merge_reviews(['$TMPDIR/group1.md', '$TMPDIR/group2.md'])
has_m1 = '[M1]' in result
has_m2 = '[M2]' in result
has_m3 = '[M3]' in result
has_m4 = '[M4]' in result
dup_count = result.count('Duplicate finding')
print(f'm1={has_m1},m2={has_m2},m3={has_m3},m4={has_m4},dup={dup_count}')
")
  [ "$result" = "m1=True,m2=True,m3=True,m4=False,dup=1" ]
}

# ── _phase_merge ─────────────────────────────────────────────────────────────

@test "_phase_merge: renders failure reasons in review gaps" {
  result=$(_py_here <<'PYEOF'
result = mod._phase_merge([], [("orc-card", "agent error: model not available"), ("svc-card", "agent hit max turns (10)")])
print(result)
PYEOF
)
  [[ "$result" == *"- orc-card: agent error: model not available"* ]]
  [[ "$result" == *"- svc-card: agent hit max turns (10)"* ]]
}

# ── _extract_section ─────────────────────────────────────────────────────────

@test "_extract_section: case-insensitive header matching" {
  result=$(_py "
content = '''## Must Fix
- [M1] bug in auth

## Should fix
- [S1] cleanup
'''
result = mod._extract_section(content, 'Must fix')
print(result)
")
  [[ "$result" == *"[M1]"* ]]
  [[ "$result" == *"bug in auth"* ]]
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

@test "merge_reviews: case-insensitive section headers merge correctly" {
  cat > "$TMPDIR/group1.md" <<'EOF'
## Must Fix
- **[M1]** a.go:10 — bug

## NIT
- **[N1]** a.go:20 — style
EOF

  result=$(_py "
result = mod.merge_reviews(['$TMPDIR/group1.md'])
print(result)
")
  [[ "$result" == *"[M1]"* ]]
  [[ "$result" == *"[N1]"* ]]
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
    session_log='/tmp/session.jsonl', reviews_dir='/tmp/reviews',
    prior_review='## Must fix\n- [ ] **[M1]** src/auth.go:10 — auth bug\n- [ ] **[M2]** src/db.go:20 — db bug',
)
result = mod.build_prompt(mod.TEMPLATE_GROUP, job, max_turns=15,
    group_idx=1, group_count=2, group_name='auth',
    group_files_formatted='src/auth.go', group_output='/tmp/out.md',
    holistic_content='', group_file_paths=['src/auth.go'],
)
print('[M1]' in result and '[M2]' not in result)
")
  [ "$result" = "True" ]
}

# ── renumber_section ──────────────────────────────────────────────────────────

@test "renumber_section: no offset keeps IDs" {
  result=$(_py '
text, count = mod.renumber_section("M", "- [M1] bug\n- [M2] other", 0)
print(f"{count}:{text}")
')
  [[ "$result" == "2:- [M1] bug"* ]]
}

@test "renumber_section: offset renumbers" {
  result=$(_py '
text, count = mod.renumber_section("S", "- [S1] fix\n- [S2] fix2", 3)
print(text)
')
  [[ "$result" == *"[S4]"* ]]
  [[ "$result" == *"[S5]"* ]]
}

@test "renumber_section: empty text returns empty" {
  result=$(_py '
text, count = mod.renumber_section("N", "", 5)
print(f"{count}:{text}")
')
  [ "$result" = "0:" ]
}


# ── _clean_triage ───────────────────────────────────────────────────────────

@test "_clean_triage: keeps only file triage entries" {
  result=$(_py "
text = '''- \`a.go\` — Tier 2
Both files are fine.

### a.go

Some narrative paragraph.
---
- \`b.go\` — Tier 1'''
result = mod._clean_triage(text)
print(result)
")
  [[ "$result" == *'`a.go`'* ]]
  [[ "$result" == *'`b.go`'* ]]
  [[ "$result" != *"Both files"* ]]
  [[ "$result" != *"###"* ]]
  [[ "$result" != *"---"* ]]
  [[ "$result" != *"narrative"* ]]
}

# ── _dedup_triage ───────────────────────────────────────────────────────────

@test "_dedup_triage: removes duplicate file entries" {
  result=$(_py "
text = '''- \`shared.go\` — Tier 1 (security)
- \`a.go\` — Tier 2
- \`shared.go\` — Tier 1 (security)
- \`b.go\` — Tier 2'''
result = mod._dedup_triage(text)
count = result.count('\`shared.go\`')
total = len([l for l in result.split(chr(10)) if l.strip()])
print(f'count={count},total={total}')
")
  [ "$result" = "count=1,total=3" ]
}

# ── _count_findings / _mechanical_verdict ────────────────────────────────────

@test "_count_findings: counts by prefix" {
  result=$(_py "
text = '''- **[M1]** finding a
- **[M2]** finding b
- **[S1]** finding c
- **[N1]** finding d
- **[N2]** finding e
- **[N3]** finding f
- **[I1]** finding g'''
counts = mod._count_findings(text)
print(f\"M={counts['M']},S={counts['S']},N={counts['N']},I={counts['I']}\")
")
  [ "$result" = "M=2,S=1,N=3,I=1" ]
}

@test "mechanical_verdict: must-fix triggers request changes" {
  result=$(_py "
counts = {'M': 2, 'S': 1, 'N': 0, 'I': 0}
print(mod.mechanical_verdict(counts))
")
  [[ "$result" == *"Request changes"* ]]
  [[ "$result" == *"2 must-fix"* ]]
}

@test "mechanical_verdict: should-fix only triggers needs discussion" {
  result=$(_py "
counts = {'M': 0, 'S': 3, 'N': 1, 'I': 0}
print(mod.mechanical_verdict(counts))
")
  [[ "$result" == *"Needs discussion"* ]]
}

@test "mechanical_verdict: nits only triggers approve" {
  result=$(_py "
counts = {'M': 0, 'S': 0, 'N': 2, 'I': 1}
print(mod.mechanical_verdict(counts))
")
  [[ "$result" == *"Approve"* ]]
  [[ "$result" == *"2 nit"* ]]
}

@test "mechanical_verdict: no findings triggers approve" {
  result=$(_py "
counts = {'M': 0, 'S': 0, 'N': 0, 'I': 0}
print(mod.mechanical_verdict(counts))
")
  [[ "$result" == *"Approve"* ]]
  [[ "$result" == *"no findings"* ]]
}
