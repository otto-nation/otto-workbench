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

@test "collect_preflight_data: returns None for oversized data" {
  result=$(_py "
import tempfile, os
from pathlib import Path

with tempfile.TemporaryDirectory() as td:
    # Create a git repo with a large file
    os.system(f'cd {td} && git init -q && git checkout -b main')
    large_file = Path(td) / 'big.txt'
    large_file.write_text('x' * 600000)
    os.system(f'cd {td} && git add . && git commit -q -m init')

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
    result = mod.collect_preflight_data(job)
    print('None' if result is None else 'data')
")
  [ "$result" = "None" ]
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

@test "build_prompt: env_section updated when preflight present" {
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
has_new_msg = "NOT in the PR" in result
has_old_msg = "Read source files directly" in result
print(f"new={has_new_msg},old={has_old_msg}")
')
  [ "$result" = "new=True,old=False" ]
}
