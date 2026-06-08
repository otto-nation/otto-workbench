#!/usr/bin/env bats
# Tests for review-orchestrate Python script — tier classification, file grouping,
# review merging, template rendering, and CLI interface.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  ORCHESTRATE="$REPO_ROOT/ai/claude/bin/review-orchestrate"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: run Python expression importing from the orchestrate script
_py() {
  python3 -c "
import sys, importlib.util, importlib.machinery
loader = importlib.machinery.SourceFileLoader('orch', '$ORCHESTRATE')
spec = importlib.util.spec_from_loader('orch', loader)
mod = importlib.util.module_from_spec(spec)
# Prevent main() from running on import
sys.modules['orch'] = mod
spec.loader.exec_module(mod)
$1
"
}

# Helper: like _py but reads code from stdin (heredoc-safe for the nesting validator)
_py_here() {
  local code
  code=$(cat)
  _py "$code"
}

# ── CLI ───────────────────────────────────────────────────────────────────────

@test "review-orchestrate --help exits 0" {
  run "$ORCHESTRATE" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"Review orchestration"* ]]
}

@test "review-orchestrate missing required args exits non-zero" {
  run "$ORCHESTRATE" --repo foo/bar
  [ "$status" -ne 0 ]
}

# ── Tier classification ──────────────────────────────────────────────────────

@test "classify_tier: CLAUDE.md is tier 1" {
  result=$(_py 'print(mod.classify_tier("CLAUDE.md"))')
  [ "$result" = "1" ]
}

@test "classify_tier: go.mod is tier 1" {
  result=$(_py 'print(mod.classify_tier("go.mod"))')
  [ "$result" = "1" ]
}

@test "classify_tier: auth path is tier 1" {
  result=$(_py 'print(mod.classify_tier("pkg/auth/handler.go"))')
  [ "$result" = "1" ]
}

@test "classify_tier: migrations path is tier 1" {
  result=$(_py 'print(mod.classify_tier("db/migrations/001_init.sql"))')
  [ "$result" = "1" ]
}

@test "classify_tier: proto file is tier 1" {
  result=$(_py 'print(mod.classify_tier("api/service.proto"))')
  [ "$result" = "1" ]
}

@test "classify_tier: regular go file is tier 2" {
  result=$(_py 'print(mod.classify_tier("pkg/service/handler.go"))')
  [ "$result" = "2" ]
}

@test "classify_tier: gen path is tier 3" {
  result=$(_py 'print(mod.classify_tier("pkg/gen/models.go"))')
  [ "$result" = "3" ]
}

@test "classify_tier: pb.go file is tier 3" {
  result=$(_py 'print(mod.classify_tier("api/service.pb.go"))')
  [ "$result" = "3" ]
}

@test "classify_tier: go.sum is tier 3" {
  result=$(_py 'print(mod.classify_tier("go.sum"))')
  [ "$result" = "3" ]
}

@test "classify_tier: testdata is tier 3" {
  result=$(_py 'print(mod.classify_tier("pkg/testdata/fixture.json"))')
  [ "$result" = "3" ]
}

# ── File grouping ─────────────────────────────────────────────────────────────

@test "group_files: separates tiers" {
  result=$(_py '
pr = mod.PRMetadata(
    title="test", body="", head="feat", base="main", head_sha="abc",
    additions=100, deletions=50, changed_files=3,
    files=[
        {"path": "CLAUDE.md", "additions": 10, "deletions": 5},
        {"path": "pkg/handler.go", "additions": 40, "deletions": 20},
        {"path": "pkg/gen/models.go", "additions": 50, "deletions": 25},
    ],
)
groups = mod.group_files(pr)
names = [g.name for g in groups]
print(",".join(names))
')
  [[ "$result" == *"tier1-critical"* ]]
  [[ "$result" == *"pkg"* ]]
  [[ "$result" == *"tier3-generated"* ]]
}

@test "group_files: splits large directories" {
  result=$(_py '
files = [{"path": f"pkg/file{i}.go", "additions": 500, "deletions": 0} for i in range(3)]
pr = mod.PRMetadata(
    title="test", body="", head="feat", base="main", head_sha="abc",
    additions=1500, deletions=0, changed_files=3, files=files,
)
groups = mod.group_files(pr)
names = [g.name for g in groups]
print(",".join(names))
')
  [[ "$result" == *"pkg-1"* ]]
  [[ "$result" == *"pkg-2"* ]]
}

# ── Group merging ────────────────────────────────────────────────────────────

@test "_merge_smallest_groups: reduces count to max" {
  result=$(_py '
groups = [
    mod.Group("a", ["a.go"], 100),
    mod.Group("b", ["b.go"], 200),
    mod.Group("c", ["c.go"], 50),
    mod.Group("d", ["d.go"], 150),
    mod.Group("e", ["e.go"], 75),
]
merged = mod._merge_smallest_groups(groups, 3)
print(len(merged))
')
  [ "$result" = "3" ]
}

@test "_merge_smallest_groups: merges smallest groups first" {
  result=$(_py '
groups = [
    mod.Group("big", ["big.go"], 500),
    mod.Group("small1", ["s1.go"], 10),
    mod.Group("small2", ["s2.go"], 20),
    mod.Group("medium", ["m.go"], 100),
]
merged = mod._merge_smallest_groups(groups, 3)
names = [g.name for g in merged]
lines = [g.lines for g in merged]
print(sorted(names))
print(sorted(lines))
')
  # small1 (10) + small2 (20) should be merged into one group (30 lines)
  [[ "$result" == *"30"* ]]
  [[ "$result" == *"500"* ]]
  [[ "$result" == *"100"* ]]
}

@test "_merge_smallest_groups: noop when under cap" {
  result=$(_py '
groups = [
    mod.Group("a", ["a.go"], 100),
    mod.Group("b", ["b.go"], 200),
]
merged = mod._merge_smallest_groups(groups, 5)
print(len(merged))
')
  [ "$result" = "2" ]
}

# ── Review merging ────────────────────────────────────────────────────────────

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
result = mod.build_prompt(mod.TEMPLATE_GROUP, job,
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

# ── Template rendering ────────────────────────────────────────────────────────

@test "render_template: substitutes variables" {
  result=$(_py '
result = mod.render_template("single-agent.md",
    pr_number="42", repo="test/repo",
    pr_header="## PR metadata", reviews_section="## Reviews",
    env_section="## Env", issue_section="", prior_section="",
    review_file="/tmp/review.md",
)
print(result)
')
  [[ "$result" == *"PR #42"* ]]
  [[ "$result" == *"test/repo"* ]]
}

@test "render_template: safe_substitute leaves unknown vars" {
  result=$(_py '
result = mod.render_template("single-agent.md",
    pr_number="1", repo="r",
    pr_header="h", reviews_section="r",
    env_section="e", review_file="f",
)
print("ok" if "${" not in result or "issue_section" in result else "fail")
')
  [ "$result" = "ok" ]
}

# ── Agent command building ───────────────────────────────────────────────────

@test "_build_agent_cmd: includes max-turns when set" {
  result=$(_py '
cmd = mod._build_agent_cmd("/tmp/reviews", "/tmp/wt", max_turns=10)
print("--max-turns" in cmd, cmd[cmd.index("--max-turns") + 1] if "--max-turns" in cmd else "")
')
  [[ "$result" == *"True"* ]]
  [[ "$result" == *"10"* ]]
}

@test "_build_agent_cmd: includes max-budget-usd when set" {
  result=$(_py '
cmd = mod._build_agent_cmd("/tmp/reviews", "/tmp/wt", max_budget=5.0)
print("--max-budget-usd" in cmd, cmd[cmd.index("--max-budget-usd") + 1] if "--max-budget-usd" in cmd else "")
')
  [[ "$result" == *"True"* ]]
  [[ "$result" == *"5.0"* ]]
}

@test "_build_agent_cmd: omits flags when None" {
  result=$(_py '
cmd = mod._build_agent_cmd("/tmp/reviews", "/tmp/wt")
print("--max-turns" not in cmd and "--max-budget-usd" not in cmd)
')
  [ "$result" = "True" ]
}

@test "_build_agent_cmd: includes model when set" {
  result=$(_py '
cmd = mod._build_agent_cmd("/tmp/reviews", "/tmp/wt", model="sonnet")
print("--model" in cmd, cmd[cmd.index("--model") + 1] if "--model" in cmd else "")
')
  [[ "$result" == *"True"* ]]
  [[ "$result" == *"sonnet"* ]]
}

# ── Cost tracking ────────────────────────────────────────────────────────────

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

# ── Pipeline resume ─────────────────────────────────────────────────────────

@test "_resolve_resume: returns fresh state when no pipeline file exists" {
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
cost, skip_groups, skip_hol, state = mod._resolve_resume(job, groups)
print(cost, skip_groups, skip_hol, state)
PYEOF
)
  [ "$result" = "0.0 None False None" ]
}

@test "_resolve_resume: auto-resumes when valid pipeline state exists" {
  cat > "$TMPDIR/test.pipeline.json" <<'EOF'
{"head_sha": "abc123", "group_names": ["g1", "g2"], "holistic_done": true, "groups_done": [1]}
EOF
  result=$(_py_here <<PYEOF
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=mod.PRMetadata(
        title="t", body="", head="b", base="main", head_sha="abc123",
        additions=10, deletions=5, changed_files=2, files=[]),
    ctx=mod.PRContext(), wt_path="/tmp", review_file="$TMPDIR/test.md",
    session_log="/tmp/log.jsonl", reviews_dir="/tmp/reviews",
)
groups = [mod.Group("g1", ["a.go"], 10), mod.Group("g2", ["b.go"], 20)]
cost, skip_groups, skip_hol, state = mod._resolve_resume(job, groups)
print(skip_groups, skip_hol, state is not None)
PYEOF
)
  # _info prints a status line to stdout; check last line for the actual result
  last_line=$(echo "$result" | tail -1)
  [ "$last_line" = "{1} True True" ]
}

@test "_resolve_resume: starts fresh when SHA differs" {
  cat > "$TMPDIR/stale.pipeline.json" <<'EOF'
{"head_sha": "old_sha", "group_names": ["g1"], "holistic_done": true, "groups_done": [1]}
EOF
  result=$(_py_here <<PYEOF
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=mod.PRMetadata(
        title="t", body="", head="b", base="main", head_sha="new_sha",
        additions=10, deletions=5, changed_files=2, files=[]),
    ctx=mod.PRContext(), wt_path="/tmp", review_file="$TMPDIR/stale.md",
    session_log="/tmp/log.jsonl", reviews_dir="/tmp/reviews",
)
groups = [mod.Group("g1", ["a.go"], 10)]
cost, skip_groups, skip_hol, state = mod._resolve_resume(job, groups)
print(state)
PYEOF
)
  last_line=$(echo "$result" | tail -1)
  [ "$last_line" = "None" ]
}

# ── Prompt building ──────────────────────────────────────────────────────────

# ── Model selection ───────────────────────────────────────────────────────────

@test "model defaults: group uses sonnet, others use opus" {
  result=$(_py "
print(mod.DEFAULT_MODEL_GROUP)
print(mod.DEFAULT_MODEL_HOLISTIC)
print(mod.DEFAULT_MODEL_SYNTHESIS)
print(mod.DEFAULT_MODEL_SINGLE)
")
  lines=()
  while IFS= read -r line; do lines+=("$line"); done <<< "$result"
  [ "${lines[0]}" = "sonnet" ]
  [ "${lines[1]}" = "opus" ]
  [ "${lines[2]}" = "opus" ]
  [ "${lines[3]}" = "opus" ]
}

@test "_resolve_model: explicit override wins" {
  result=$(_py "
import os
os.environ.pop('CLAUDE_REVIEW_MODEL', None)
os.environ.pop('CLAUDE_REVIEW_GROUP_MODEL', None)
print(mod._resolve_model('haiku', 'CLAUDE_REVIEW_GROUP_MODEL', 'sonnet'))
")
  [ "$result" = "haiku" ]
}

@test "_resolve_model: env var beats default" {
  result=$(_py "
import os
os.environ['CLAUDE_REVIEW_GROUP_MODEL'] = 'haiku'
os.environ.pop('CLAUDE_REVIEW_MODEL', None)
print(mod._resolve_model(None, 'CLAUDE_REVIEW_GROUP_MODEL', 'sonnet'))
del os.environ['CLAUDE_REVIEW_GROUP_MODEL']
")
  [ "$result" = "haiku" ]
}

@test "_resolve_model: global env var beats default" {
  result=$(_py "
import os
os.environ['CLAUDE_REVIEW_MODEL'] = 'haiku'
os.environ.pop('CLAUDE_REVIEW_GROUP_MODEL', None)
print(mod._resolve_model(None, 'CLAUDE_REVIEW_GROUP_MODEL', 'sonnet'))
del os.environ['CLAUDE_REVIEW_MODEL']
")
  [ "$result" = "haiku" ]
}

@test "_resolve_model: default when no override" {
  result=$(_py "
import os
os.environ.pop('CLAUDE_REVIEW_MODEL', None)
os.environ.pop('CLAUDE_REVIEW_GROUP_MODEL', None)
print(mod._resolve_model(None, 'CLAUDE_REVIEW_GROUP_MODEL', 'sonnet'))
")
  [ "$result" = "sonnet" ]
}

# ── Prompt building ──────────────────────────────────────────────────────────

@test "build_prompt: single-agent includes review file" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="desc", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=2,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext(commits="fix it", reviews="[]", review_comments="[]", comments="[]")
job = mod.ReviewJob(
    repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
)
result = mod.build_prompt("single-agent.md", job)
print(result)
')
  [[ "$result" == *"PR #99"* ]]
  [[ "$result" == *"/tmp/review.md"* ]]
}

@test "build_prompt: group includes holistic block when content set" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
)
result = mod.build_prompt("group.md", job,
    group_idx=1, group_count=3, group_name="pkg",
    group_files_formatted="  - a.go (+10 -5)",
    group_output="/tmp/g1.md",
    holistic_content="Watch for API mismatches",
)
print("FOUND" if "Holistic context" in result else "MISSING")
')
  [ "$result" = "FOUND" ]
}

@test "build_prompt: group omits holistic block when empty" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
)
result = mod.build_prompt("group.md", job,
    group_idx=1, group_count=3, group_name="pkg",
    group_files_formatted="  - a.go (+10 -5)",
    group_output="/tmp/g1.md",
    holistic_content="",
)
print("FOUND" if "Holistic context" in result else "MISSING")
')
  [ "$result" = "MISSING" ]
}

# ── Pre-flight data collection ───────────────────────────────────────────────

@test "_read_file_safe: reads normal file" {
  echo "hello world" > "$TMPDIR/normal.txt"
  result=$(_py "
from pathlib import Path
print(mod._read_file_safe(Path('$TMPDIR/normal.txt')))
")
  [ "$result" = "hello world" ]
}

@test "_read_file_safe: handles missing file" {
  result=$(_py "
from pathlib import Path
print(mod._read_file_safe(Path('$TMPDIR/nonexistent.txt')))
")
  [ "$result" = "<file deleted>" ]
}

@test "_read_file_safe: handles binary file" {
  python3 -c "import sys; sys.stdout.buffer.write(bytes([0x80, 0x81, 0xFF, 0xFE]))" > "$TMPDIR/binary.bin"
  result=$(_py "
from pathlib import Path
print(mod._read_file_safe(Path('$TMPDIR/binary.bin')))
")
  [[ "$result" == *"binary file"* ]]
}

@test "_read_file_safe: truncates large file" {
  python3 -c "print('x' * 80 + '\n' for _ in range(2000), sep='', end='')" > "$TMPDIR/large.txt" 2>/dev/null || \
    python3 -c "
for i in range(2000):
    print('x' * 80)
" > "$TMPDIR/large.txt"
  result=$(_py "
from pathlib import Path
content = mod._read_file_safe(Path('$TMPDIR/large.txt'))
print('truncated' if 'truncated' in content else 'full')
")
  [ "$result" = "truncated" ]
}

@test "format_preflight_data: includes all sections" {
  result=$(_py '
data = mod.PreflightData(
    diff="--- a/foo.go\n+++ b/foo.go\n@@ -1 +1 @@\n-old\n+new",
    commit_log="abc123 fix bug",
    file_contents={"foo.go": "package main\n", "bar.go": "package bar\n"},
    file_permissions={"foo.go": "0o644", "bar.go": "0o755"},
    claude_md="# My Project",
    context_md="## Known Constraints",
    review_checklists={"security.md": "# Security checks"},
)
result = mod.format_preflight_data(data)
checks = [
    "Pre-collected data" in result,
    "```diff" in result,
    "foo.go" in result,
    "bar.go" in result,
    "# My Project" in result,
    "Known Constraints" in result,
    "Security checks" in result,
    "abc123 fix bug" in result,
]
print("ok" if all(checks) else "fail")
')
  [ "$result" = "ok" ]
}

@test "format_preflight_data: file_filter scopes file contents" {
  result=$(_py '
data = mod.PreflightData(
    diff="full diff",
    commit_log="log",
    file_contents={"foo.go": "package main", "bar.go": "package bar"},
    file_permissions={"foo.go": "0o644", "bar.go": "0o755"},
    claude_md="",
    context_md="",
)
result = mod.format_preflight_data(data, file_filter=["foo.go"])
has_foo = "package main" in result
has_bar = "package bar" in result
print(f"foo={has_foo},bar={has_bar}")
')
  [ "$result" = "foo=True,bar=False" ]
}

@test "_scope_diff: returns only matching file hunks" {
  result=$(_py '
diff_text = """diff --git a/foo.go b/foo.go
index 123..456 100644
--- a/foo.go
+++ b/foo.go
@@ -1,3 +1,4 @@
 package main
+import "fmt"

diff --git a/bar.go b/bar.go
index 789..abc 100644
--- a/bar.go
+++ b/bar.go
@@ -1,3 +1,3 @@
-package old
+package bar

diff --git a/baz.go b/baz.go
index def..012 100644
--- a/baz.go
+++ b/baz.go
@@ -1 +1 @@
-old
+new
"""
result = mod._scope_diff(diff_text, ["foo.go", "baz.go"])
has_foo = "foo.go" in result
has_bar = "bar.go" in result
has_baz = "baz.go" in result
print(f"foo={has_foo},bar={has_bar},baz={has_baz}")
')
  [ "$result" = "foo=True,bar=False,baz=True" ]
}

@test "_scope_diff: no matches returns empty" {
  result=$(_py '
diff_text = """diff --git a/foo.go b/foo.go
--- a/foo.go
+++ b/foo.go
@@ -1 +1 @@
-old
+new
"""
result = mod._scope_diff(diff_text, ["other.go"])
print(repr(result))
')
  [ "$result" = "''" ]
}

@test "_scope_diff: all files match returns full diff" {
  result=$(_py '
diff_text = """diff --git a/foo.go b/foo.go
--- a/foo.go
+++ b/foo.go
@@ -1 +1 @@
-old
+new
"""
result = mod._scope_diff(diff_text, ["foo.go"])
print("foo.go" in result)
')
  [ "$result" = "True" ]
}

@test "format_preflight_data: file_filter scopes diff" {
  result=$(_py '
diff_text = """diff --git a/foo.go b/foo.go
--- a/foo.go
+++ b/foo.go
@@ -1 +1 @@
-old
+new

diff --git a/bar.go b/bar.go
--- a/bar.go
+++ b/bar.go
@@ -1 +1 @@
-old
+new
"""
data = mod.PreflightData(
    diff=diff_text,
    commit_log="log",
    file_contents={"foo.go": "package main", "bar.go": "package bar"},
    file_permissions={"foo.go": "0o644", "bar.go": "0o755"},
    claude_md="",
    context_md="",
)
result = mod.format_preflight_data(data, file_filter=["foo.go"])
has_foo_diff = "a/foo.go" in result
has_bar_diff = "a/bar.go" in result
print(f"foo_diff={has_foo_diff},bar_diff={has_bar_diff}")
')
  [ "$result" = "foo_diff=True,bar_diff=False" ]
}

@test "collect_preflight_data: oversized file included in diff but omitted from contents" {
  result=$(_py_here <<'PYEOF'
import tempfile, os
from pathlib import Path

with tempfile.TemporaryDirectory() as td:
    os.system(f'cd {td} && git init -q && git checkout -b main')
    os.system(f'cd {td} && git config user.email test@test.com && git config user.name Test && git config commit.gpgsign false')
    large_file = Path(td) / 'big.txt'
    large_file.write_text('x' * 600000)
    os.system(f'cd {td} && git add . && git commit -q --no-verify -m init')
    os.system(f'cd {td} && git remote add origin {td} && git fetch -q origin main')
    os.system(f'cd {td} && git checkout -b feat -q')
    large_file.write_text('y' * 600000)
    os.system(f'cd {td} && git add . && git commit -q --no-verify -m change')

    pr = mod.PRMetadata(
        title='t', body='', head='feat', base='main', head_sha='abc',
        additions=1, deletions=0, changed_files=1,
        files=[{'path': 'big.txt', 'additions': 1, 'deletions': 0}],
    )
    ctx = mod.PRContext()
    job = mod.ReviewJob(
        repo='r', pr_number='1', pr=pr, ctx=ctx,
        wt_path=td, review_file='/tmp/r.md',
        session_log='/tmp/s.jsonl', reviews_dir='/tmp/rev',
    )
    import io, contextlib
    with contextlib.redirect_stdout(io.StringIO()):
        result = mod.collect_preflight_data(job)
    checks = [
        result is not None,
        len(result.diff) > 0,
        result.omitted_files == ['big.txt'],
        result.file_contents == {},
    ]
    print('ok' if all(checks) else [i for i,c in enumerate(checks) if not c])
PYEOF
)
  [ "$result" = "ok" ]
}

@test "collect_preflight_data: large diff+files includes diff but omits some files" {
  repo="$TMPDIR/budget-repo"
  mkdir -p "$repo"
  cd "$repo"
  git init -q && git checkout -b main -q
  git config user.email "test@test.com" && git config user.name "Test"
  git config commit.gpgsign false
  # Create 5 files with 10k lines each to produce a large diff
  python3 <<'PYEOF'
for i in range(1, 6):
    with open(f'file{i}.go', 'w') as f:
        for j in range(10000):
            f.write(f'original_line_content_padding_{j}\n')
PYEOF
  git add . && git commit -q --no-verify -m "init"
  git remote add origin "$repo" && git fetch -q origin main
  git checkout -b feat -q
  python3 <<'PYEOF'
for i in range(1, 6):
    with open(f'file{i}.go', 'w') as f:
        for j in range(10000):
            f.write(f'modified_line_content_padding_{j}\n')
PYEOF
  git add . && git commit -q --no-verify -m "change"

  result=$(_py_here <<PYEOF
pr = mod.PRMetadata(
    title='t', body='', head='feat', base='main', head_sha='abc',
    additions=50000, deletions=50000, changed_files=5,
    files=[
        {'path': f'file{i}.go', 'additions': 10000, 'deletions': 10000}
        for i in range(1, 6)
    ],
)
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo='r', pr_number='1', pr=pr, ctx=ctx,
    wt_path='$repo', review_file='/tmp/r.md',
    session_log='/tmp/s.jsonl', reviews_dir='/tmp/rev',
)
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    result = mod.collect_preflight_data(job)
checks = [
    result is not None,
    len(result.diff) > 0,
    len(result.omitted_files) > 0,
    len(result.file_contents) + len(result.omitted_files) == 5,
]
print('ok' if all(checks) else [i for i,c in enumerate(checks) if not c])
PYEOF
)
  [ "$result" = "ok" ]
}

@test "build_prompt: includes preflight_data when set" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="desc", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext(commits="fix it", reviews="[]", review_comments="[]", comments="[]")
preflight = mod.PreflightData(
    diff="--- a/a.go\n+++ b/a.go",
    commit_log="abc fix",
    file_contents={"a.go": "package main"},
    file_permissions={"a.go": "0o644"},
    claude_md="# Project",
    context_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("single-agent.md", job)
has_preflight = "Pre-collected data" in result
has_file = "package main" in result
has_diff = "--- a/a.go" in result
print(f"preflight={has_preflight},file={has_file},diff={has_diff}")
')
  [ "$result" = "preflight=True,file=True,diff=True" ]
}

@test "build_prompt: no preflight_data when not set" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="desc", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext(commits="fix it", reviews="[]", review_comments="[]", comments="[]")
job = mod.ReviewJob(
    repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
)
result = mod.build_prompt("single-agent.md", job)
print("absent" if "Pre-collected data" not in result else "present")
')
  [ "$result" = "absent" ]
}

@test "build_prompt: synthesis includes reviews_section" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext(commits="fix", reviews="[{\"user\":\"bob\",\"state\":\"APPROVED\"}]",
    review_comments="[]", comments="[]")
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
)
result = mod.build_prompt("synthesis.md", job,
    holistic_content="assessment",
    group_count=1,
    merged_content="## Must fix\n- [M1] bug",
)
print("FOUND" if "bob" in result and "APPROVED" in result else "MISSING")
')
  [ "$result" = "FOUND" ]
}

@test "_file_permissions: returns octal for normal file" {
  echo "test" > "$TMPDIR/perm.txt"
  chmod 644 "$TMPDIR/perm.txt"
  result=$(_py "
from pathlib import Path
print(mod._file_permissions(Path('$TMPDIR/perm.txt')))
")
  [ "$result" = "0o644" ]
}

@test "_file_permissions: returns ? for missing file" {
  result=$(_py "
from pathlib import Path
print(mod._file_permissions(Path('$TMPDIR/nonexistent.txt')))
")
  [ "$result" = "?" ]
}

@test "_file_permissions: returns executable mode" {
  echo "#!/bin/sh" > "$TMPDIR/exec.sh"
  chmod 755 "$TMPDIR/exec.sh"
  result=$(_py "
from pathlib import Path
print(mod._file_permissions(Path('$TMPDIR/exec.sh')))
")
  [ "$result" = "0o755" ]
}

@test "collect_preflight_data: success path collects all data" {
  # Create a temp git repo with origin/main ref for diff/log range queries
  repo="$TMPDIR/repo"
  mkdir -p "$repo/.claude/review"
  cd "$repo"
  git init -q && git checkout -b main -q
  git config user.email "test@test.com" && git config user.name "Test"
  git config commit.gpgsign false
  echo "package main" > "$repo/main.go"
  echo "# Project" > "$repo/CLAUDE.md"
  echo "## Known Constraints" > "$repo/.claude/context.md"
  echo "# Security" > "$repo/.claude/review/security.md"
  git add . && git commit -q --no-verify -m "init"
  # Create a fake origin/main ref so git diff origin/main...HEAD works
  git remote add origin "$repo" && git fetch -q origin main
  git checkout -b feat -q
  printf "package main\nfunc hello() {}\n" > "$repo/main.go"
  git add . && git commit -q --no-verify -m "add hello"

  result=$(_py_here <<PYEOF
from pathlib import Path
pr = mod.PRMetadata(
    title='t', body='', head='feat', base='main', head_sha='abc',
    additions=1, deletions=0, changed_files=1,
    files=[{'path': 'main.go', 'additions': 1, 'deletions': 0}],
)
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo='r', pr_number='1', pr=pr, ctx=ctx,
    wt_path='$repo', review_file='/tmp/r.md',
    session_log='/tmp/s.jsonl', reviews_dir='/tmp/rev',
)
data = mod.collect_preflight_data(job)
checks = [
    data is not None,
    'main.go' in data.file_contents,
    'main.go' in data.file_permissions,
    data.file_permissions['main.go'] != '?',
    '# Project' in data.claude_md,
    '## Known Constraints' in data.context_md,
    'security.md' in data.review_checklists,
    len(data.diff) > 0,
    len(data.commit_log) > 0,
    data.omitted_files == [],
]
print('ok' if all(checks) else [i for i, c in enumerate(checks) if not c])
PYEOF
)
  [ "$result" = "ok" ]
}

@test "collect_preflight_data: handles deleted files in PR" {
  repo="$TMPDIR/repo_del"
  mkdir -p "$repo"
  cd "$repo"
  git init -q && git checkout -b main -q
  git config user.email "test@test.com" && git config user.name "Test"
  git config commit.gpgsign false
  echo "old content" > "$repo/removed.txt"
  echo "keep" > "$repo/kept.txt"
  git add . && git commit -q --no-verify -m "init"
  git checkout -b feat -q
  rm "$repo/removed.txt"
  echo "updated" > "$repo/kept.txt"
  git add . && git commit -q --no-verify -m "remove file"

  result=$(_py "
pr = mod.PRMetadata(
    title='t', body='', head='feat', base='main', head_sha='abc',
    additions=1, deletions=1, changed_files=2,
    files=[
        {'path': 'removed.txt', 'additions': 0, 'deletions': 1},
        {'path': 'kept.txt', 'additions': 1, 'deletions': 0},
    ],
)
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo='r', pr_number='1', pr=pr, ctx=ctx,
    wt_path='$repo', review_file='/tmp/r.md',
    session_log='/tmp/s.jsonl', reviews_dir='/tmp/rev',
)
data = mod.collect_preflight_data(job)
removed_ok = data.file_contents['removed.txt'] == '<file deleted>'
kept_ok = 'updated' in data.file_contents['kept.txt']
print(f'removed={removed_ok},kept={kept_ok}')
")
  [ "$result" = "removed=True,kept=True" ]
}

@test "format_preflight_data: empty commit_log omits section" {
  result=$(_py '
data = mod.PreflightData(
    diff="--- a/f.go\n+++ b/f.go",
    commit_log="",
    file_contents={"f.go": "code"},
    file_permissions={"f.go": "0o644"},
    claude_md="",
    context_md="",
)
result = mod.format_preflight_data(data)
print("absent" if "Commit history" not in result else "present")
')
  [ "$result" = "absent" ]
}

@test "build_prompt: GROUP template gets scoped preflight" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=20, deletions=10, changed_files=2,
    files=[
        {"path": "a.go", "additions": 10, "deletions": 5},
        {"path": "b.go", "additions": 10, "deletions": 5},
    ],
)
ctx = mod.PRContext()
preflight = mod.PreflightData(
    diff="full diff here",
    commit_log="commits",
    file_contents={"a.go": "package a", "b.go": "package b"},
    file_permissions={"a.go": "0o644", "b.go": "0o644"},
    claude_md="",
    context_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("group.md", job,
    group_idx=1, group_count=2, group_name="pkg",
    group_files_formatted="  - a.go (+10 -5)",
    group_output="/tmp/g1.md",
    holistic_content="",
    group_file_paths=["a.go"],
)
has_a = "package a" in result
has_b = "package b" in result
print(f"a={has_a},b={has_b}")
')
  [ "$result" = "a=True,b=False" ]
}

@test "build_prompt: holistic template includes preflight" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext(commits="fix", reviews="[]", review_comments="[]", comments="[]")
preflight = mod.PreflightData(
    diff="--- a/a.go\n+++ b/a.go",
    commit_log="abc fix",
    file_contents={"a.go": "package main"},
    file_permissions={"a.go": "0o644"},
    claude_md="# Proj",
    context_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("holistic.md", job, holistic_output="/tmp/h.md")
has_preflight = "Pre-collected data" in result
has_file = "package main" in result
print(f"preflight={has_preflight},file={has_file}")
')
  [ "$result" = "preflight=True,file=True" ]
}

@test "build_prompt: self-review template includes preflight" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext()
preflight = mod.PreflightData(
    diff="--- a/a.go\n+++ b/a.go",
    commit_log="abc fix",
    file_contents={"a.go": "package main"},
    file_permissions={"a.go": "0o644"},
    claude_md="",
    context_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("self-review.md", job, branch_name="feat")
has_preflight = "Pre-collected data" in result
has_file = "package main" in result
print(f"preflight={has_preflight},file={has_file}")
')
  [ "$result" = "preflight=True,file=True" ]
}

@test "build_prompt: env_section — all files pre-collected" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext(commits="fix", reviews="[]", review_comments="[]", comments="[]")
preflight = mod.PreflightData(
    diff="diff", commit_log="log",
    file_contents={"a.go": "pkg"},
    file_permissions={"a.go": "0o644"},
    claude_md="", context_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("single-agent.md", job)
has_full = "NOT in the PR" in result
has_partial = "Files not pre-collected" in result
has_none = "Read source files directly" in result
print(f"full={has_full},partial={has_partial},none={has_none}")
')
  [ "$result" = "full=True,partial=False,none=False" ]
}

@test "build_prompt: env_section — partial preflight with omitted files" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=2,
    files=[
        {"path": "a.go", "additions": 5, "deletions": 2},
        {"path": "b.go", "additions": 5, "deletions": 3},
    ],
)
ctx = mod.PRContext(commits="fix", reviews="[]", review_comments="[]", comments="[]")
preflight = mod.PreflightData(
    diff="diff", commit_log="log",
    file_contents={"a.go": "pkg"},
    file_permissions={"a.go": "0o644"},
    claude_md="", context_md="",
    omitted_files=["b.go"],
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("single-agent.md", job)
has_full = "NOT in the PR" in result
has_partial = "must be read directly" in result
has_none = "Read source files directly" in result
print(f"full={has_full},partial={has_partial},none={has_none}")
')
  [ "$result" = "full=False,partial=True,none=False" ]
}

@test "build_prompt: env_section — no preflight" {
  result=$(_py '
pr = mod.PRMetadata(
    title="Fix", body="", head="feat", base="main", head_sha="abc",
    additions=10, deletions=5, changed_files=1,
    files=[{"path": "a.go", "additions": 10, "deletions": 5}],
)
ctx = mod.PRContext(commits="fix", reviews="[]", review_comments="[]", comments="[]")
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
)
result = mod.build_prompt("single-agent.md", job)
has_full = "NOT in the PR" in result
has_partial = "must be read directly" in result
has_none = "Read source files directly" in result
print(f"full={has_full},partial={has_partial},none={has_none}")
')
  [ "$result" = "full=False,partial=False,none=True" ]
}

@test "format_preflight_data: omitted_files listed in output" {
  result=$(_py '
data = mod.PreflightData(
    diff="--- a/a.go\n+++ b/a.go",
    commit_log="log",
    file_contents={"a.go": "code"},
    file_permissions={"a.go": "0o644"},
    claude_md="", context_md="",
    omitted_files=["big.go", "huge.go"],
)
result = mod.format_preflight_data(data)
has_section = "Files not pre-collected" in result
has_big = "- big.go" in result
has_huge = "- huge.go" in result
has_a = "a.go" in result
print(f"section={has_section},big={has_big},huge={has_huge},a={has_a}")
')
  [ "$result" = "section=True,big=True,huge=True,a=True" ]
}

@test "format_preflight_data: no omitted section when all files included" {
  result=$(_py '
data = mod.PreflightData(
    diff="--- a/a.go\n+++ b/a.go",
    commit_log="log",
    file_contents={"a.go": "code"},
    file_permissions={"a.go": "0o644"},
    claude_md="", context_md="",
)
result = mod.format_preflight_data(data)
print("absent" if "Files not pre-collected" not in result else "present")
')
  [ "$result" = "absent" ]
}

@test "format_preflight_data: skip_file_contents omits file contents and omitted list" {
  result=$(_py '
data = mod.PreflightData(
    diff="--- a/foo.go\n+++ b/foo.go",
    commit_log="abc123 fix bug",
    file_contents={"foo.go": "package main"},
    file_permissions={"foo.go": "0o644"},
    claude_md="# Project",
    context_md="",
    omitted_files=["bar.go"],
)
result = mod.format_preflight_data(data, skip_file_contents=True)
checks = [
    "```diff" in result,
    "abc123 fix bug" in result,
    "# Project" in result,
    "package main" not in result,
    "Changed file contents" not in result,
    "Files not pre-collected" not in result,
]
print("ok" if all(checks) else "fail")
')
  [ "$result" = "ok" ]
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

@test "_mechanical_verdict: must-fix triggers request changes" {
  result=$(_py "
counts = {'M': 2, 'S': 1, 'N': 0, 'I': 0}
print(mod._mechanical_verdict(counts))
")
  [[ "$result" == *"Request changes"* ]]
  [[ "$result" == *"2 must-fix"* ]]
}

@test "_mechanical_verdict: should-fix only triggers needs discussion" {
  result=$(_py "
counts = {'M': 0, 'S': 3, 'N': 1, 'I': 0}
print(mod._mechanical_verdict(counts))
")
  [[ "$result" == *"Needs discussion"* ]]
}

@test "_mechanical_verdict: nits only triggers approve" {
  result=$(_py "
counts = {'M': 0, 'S': 0, 'N': 2, 'I': 1}
print(mod._mechanical_verdict(counts))
")
  [[ "$result" == *"Approve"* ]]
  [[ "$result" == *"2 nit"* ]]
}

@test "_mechanical_verdict: no findings triggers approve" {
  result=$(_py "
counts = {'M': 0, 'S': 0, 'N': 0, 'I': 0}
print(mod._mechanical_verdict(counts))
")
  [[ "$result" == *"Approve"* ]]
  [[ "$result" == *"no findings"* ]]
}

@test "collect_preflight_data: tier-1 files prioritized over tier-2 when budget tight" {
  result=$(_py "
import tempfile, os, io, contextlib
from pathlib import Path
import orch

with tempfile.TemporaryDirectory() as td:
    os.system(f'cd {td} && git init -q && git checkout -b main 2>/dev/null')
    os.system(f'cd {td} && git config user.email test@test.com && git config user.name Test && git config commit.gpgsign false')
    # tier-1 file (CLAUDE.md, small) and tier-2 file (util.go, 50KB)
    Path(td, 'CLAUDE.md').write_text('# Rules\\n' * 10)
    Path(td, 'util.go').write_text('package main\\n' + 'func f() {}\\n' * 3000)
    os.system(f'cd {td} && git add . && git commit -q --no-verify -m init 2>/dev/null')
    os.system(f'cd {td} && git remote add origin {td} && git fetch -q origin main 2>/dev/null')
    os.system(f'cd {td} && git checkout -b feat -q 2>/dev/null')
    Path(td, 'CLAUDE.md').write_text('# Updated rules\\n' * 10)
    Path(td, 'util.go').write_text('package main\\n' + 'func g() {}\\n' * 3000)
    os.system(f'cd {td} && git add . && git commit -q --no-verify -m change 2>/dev/null')

    pr = mod.PRMetadata(
        title='t', body='', head='feat', base='main', head_sha='abc',
        additions=2, deletions=2, changed_files=2,
        files=[
            {'path': 'util.go', 'additions': 3000, 'deletions': 3000},
            {'path': 'CLAUDE.md', 'additions': 10, 'deletions': 10},
        ],
    )
    ctx = mod.PRContext()
    job = mod.ReviewJob(
        repo='r', pr_number='1', pr=pr, ctx=ctx,
        wt_path=td, review_file='/tmp/r.md',
        session_log='/tmp/s.jsonl', reviews_dir='/tmp/rev',
    )
    # Set budget so diff fits but only ~200 bytes remain — enough for CLAUDE.md, not util.go
    original = orch.MAX_PROMPT_BYTES
    diff_size = len(mod._run(['git', 'diff', 'origin/main...HEAD'], cwd=td).encode())
    orch.MAX_PROMPT_BYTES = diff_size + orch.TEMPLATE_OVERHEAD_BYTES + 1000

    with contextlib.redirect_stdout(io.StringIO()):
        data = mod.collect_preflight_data(job)
    orch.MAX_PROMPT_BYTES = original

    claude_in = 'CLAUDE.md' in data.file_contents
    util_out = 'util.go' in data.omitted_files
    print(f'claude_in={claude_in},util_out={util_out}')
")
  [ "$result" = "claude_in=True,util_out=True" ]
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
  echo "not valid json" > "$TMPDIR/review.pipeline.json"
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
  echo '{"type": "result", "total_cost_usd": 1.50}' > "$TMPDIR/review.holistic.jsonl"
  echo '{"type": "result", "total_cost_usd": 0.75}' > "$TMPDIR/review.group-1.jsonl"
  echo '{"type": "result", "total_cost_usd": 0.50}' > "$TMPDIR/review.group-2.jsonl"

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

@test "SUFFIX_PIPELINE_STATE constant exists" {
  result=$(_py "print(mod.SUFFIX_PIPELINE_STATE)")
  [ "$result" = ".pipeline.json" ]
}

@test "--resume flag removed from CLI (auto-resume is default)" {
  run "$ORCHESTRATE" --help
  [[ "$output" != *"--resume"* ]]
}

@test "_consolidate_logs: merges log files without deleting intermediates" {
  echo '{"type":"result","total_cost_usd":1.0}' > "$TMPDIR/review.holistic.jsonl"
  echo '{"type":"result","total_cost_usd":0.5}' > "$TMPDIR/review.group-1.jsonl"
  echo '{"type":"result","total_cost_usd":0.3}' > "$TMPDIR/review.synthesis.jsonl"
  echo "holistic content" > "$TMPDIR/review.holistic.md"
  echo "group content" > "$TMPDIR/review.group-1.md"

  result=$(_py "
import io, contextlib
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/review.session.jsonl',
        reviews_dir='/tmp/reviews',
    )
    mod._consolidate_logs(
        job,
        holistic_log='$TMPDIR/review.holistic.jsonl',
        group_count=1,
        synthesis_log='$TMPDIR/review.synthesis.jsonl',
    )
import os
session_exists = os.path.exists('$TMPDIR/review.session.jsonl')
holistic_exists = os.path.exists('$TMPDIR/review.holistic.md')
group_exists = os.path.exists('$TMPDIR/review.group-1.md')
holistic_log_exists = os.path.exists('$TMPDIR/review.holistic.jsonl')
print(f'session={session_exists},holistic={holistic_exists},group={group_exists},hlog={holistic_log_exists}')
")
  echo "$result"
  [ "$result" = "session=True,holistic=True,group=True,hlog=True" ]
}

@test "_cleanup_intermediates: removes intermediate files and pipeline state" {
  echo "holistic" > "$TMPDIR/review.holistic.md"
  echo "log" > "$TMPDIR/review.holistic.jsonl"
  echo "group" > "$TMPDIR/review.group-1.md"
  echo "glog" > "$TMPDIR/review.group-1.jsonl"
  echo "glog2" > "$TMPDIR/review.group-2.jsonl"
  echo "synth" > "$TMPDIR/review.synthesis.jsonl"
  echo '{}' > "$TMPDIR/review.pipeline.json"

  result=$(_py "
import io, contextlib, os
with contextlib.redirect_stdout(io.StringIO()):
    job = mod.ReviewJob(
        repo='org/repo', pr_number='1',
        pr=mod.PRMetadata(title='t', body='', head='f', base='main', head_sha='abc',
            additions=1, deletions=0, changed_files=1, files=[]),
        ctx=mod.PRContext(), wt_path='/tmp/wt',
        review_file='$TMPDIR/review.md',
        session_log='$TMPDIR/review.session.jsonl',
        reviews_dir='/tmp/reviews',
    )
    mod._cleanup_intermediates(
        job,
        holistic_output='$TMPDIR/review.holistic.md',
        holistic_log='$TMPDIR/review.holistic.jsonl',
        group_outputs=['$TMPDIR/review.group-1.md'],
        group_count=2,
        synthesis_log='$TMPDIR/review.synthesis.jsonl',
    )
remaining = []
for f in ['review.holistic.md', 'review.holistic.jsonl', 'review.group-1.md',
          'review.group-1.jsonl', 'review.synthesis.jsonl', 'review.pipeline.json']:
    if os.path.exists('$TMPDIR/' + f):
        remaining.append(f)
print(f'remaining={remaining}')
")
  echo "$result"
  [ "$result" = "remaining=[]" ]
}

@test "_review_group: skip=True returns early when output exists" {
  echo "existing group review" > "$TMPDIR/review.group-1.md"

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
state_file = f"{d}/review.pipeline.json"
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
import subprocess
# Patch _build_agent_cmd to return a simple failing command
original = mod._build_agent_cmd
mod._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'echo fail >&2; exit 42']
rc = mod.invoke_agent('test', '$TMPDIR/test.jsonl', '/tmp', '/tmp')
mod._build_agent_cmd = original
print(rc)
")
  [ "$result" = "42" ]
}

@test "invoke_agent: logs stderr on failure" {
  result=$(_py "
import subprocess, os
mod._build_agent_cmd = lambda *a, **kw: ['bash', '-c', 'echo agent-error-msg >&2; exit 1']
mod.invoke_agent('test', '$TMPDIR/stderr_test.jsonl', '/tmp', '/tmp')
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

# ── Uncommitted changes (self-review before commit) ────────────────────────

@test "fetch_branch_metadata: includes uncommitted changes when no commits on branch" {
  repo="$TMPDIR/uncommitted-repo"
  mkdir -p "$repo"
  cd "$repo"
  git init -q && git checkout -b main -q
  git config user.email "test@test.com" && git config user.name "Test"
  git config commit.gpgsign false
  echo "package main" > "$repo/main.go"
  git add . && git commit -q --no-verify -m "init"
  git remote add origin "$repo" && git fetch -q origin main
  # Stay on main (no new commits) but modify a file
  printf "package main\nfunc hello() {}\n" > "$repo/main.go"

  result=$(_py "
pr = mod.fetch_branch_metadata('$repo')
print(f'files={pr.changed_files},add={pr.additions},del={pr.deletions}')
print(f'path={pr.files[0][\"path\"] if pr.files else \"none\"}')
")
  [[ "$result" == *"files=1"* ]]
  [[ "$result" == *"path=main.go"* ]]
}

@test "fetch_branch_metadata: includes staged changes when no commits on branch" {
  repo="$TMPDIR/staged-repo"
  mkdir -p "$repo"
  cd "$repo"
  git init -q && git checkout -b main -q
  git config user.email "test@test.com" && git config user.name "Test"
  git config commit.gpgsign false
  echo "package main" > "$repo/main.go"
  git add . && git commit -q --no-verify -m "init"
  git remote add origin "$repo" && git fetch -q origin main
  # Stage changes without committing
  printf "package main\nfunc staged() {}\n" > "$repo/main.go"
  git add main.go

  result=$(_py "
pr = mod.fetch_branch_metadata('$repo')
print(f'files={pr.changed_files},add={pr.additions}')
")
  [[ "$result" == *"files=1"* ]]
}

@test "fetch_branch_metadata: prefers committed diff over uncommitted when commits exist" {
  repo="$TMPDIR/committed-repo"
  mkdir -p "$repo"
  cd "$repo"
  git init -q && git checkout -b main -q
  git config user.email "test@test.com" && git config user.name "Test"
  git config commit.gpgsign false
  echo "package main" > "$repo/main.go"
  git add . && git commit -q --no-verify -m "init"
  git remote add origin "$repo" && git fetch -q origin main
  git checkout -b feat -q
  printf "package main\nfunc committed() {}\n" > "$repo/main.go"
  git add . && git commit -q --no-verify -m "add committed"
  # Also add an uncommitted file — should NOT appear
  echo "uncommitted" > "$repo/extra.go"

  result=$(_py "
pr = mod.fetch_branch_metadata('$repo')
paths = [f['path'] for f in pr.files]
print(f'files={pr.changed_files},paths={paths}')
")
  [[ "$result" == *"files=1"* ]]
  [[ "$result" == *"main.go"* ]]
  [[ "$result" != *"extra.go"* ]]
}

@test "collect_preflight_data: captures uncommitted diff when no commits on branch" {
  repo="$TMPDIR/preflight-uncommitted"
  mkdir -p "$repo"
  cd "$repo"
  git init -q && git checkout -b main -q
  git config user.email "test@test.com" && git config user.name "Test"
  git config commit.gpgsign false
  echo "package main" > "$repo/main.go"
  git add . && git commit -q --no-verify -m "init"
  git remote add origin "$repo" && git fetch -q origin main
  printf "package main\nfunc hello() {}\n" > "$repo/main.go"

  result=$(_py_here <<PYEOF
pr = mod.fetch_branch_metadata('$repo')
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo='r', pr_number='', pr=pr, ctx=ctx,
    wt_path='$repo', review_file='/tmp/r.md',
    session_log='/tmp/s.jsonl', reviews_dir='/tmp/rev',
    mode='self',
)
data = mod.collect_preflight_data(job)
has_diff = 'func hello' in data.diff
has_file = 'main.go' in data.file_contents
print(f'has_diff={has_diff},has_file={has_file}')
PYEOF
)
  [ "$result" = "has_diff=True,has_file=True" ]
}
