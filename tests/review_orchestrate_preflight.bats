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
    architecture_md="## Known Constraints",
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
    architecture_md="",
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
    architecture_md="",
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
    architecture_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("single-agent.md", job, max_turns=15)
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
result = mod.build_prompt("single-agent.md", job, max_turns=15)
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
result = mod.build_prompt("synthesis.md", job, max_turns=15,
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
  echo "## Known Constraints" > "$repo/.claude/architecture.md"
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
    '## Known Constraints' in data.architecture_md,
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
    architecture_md="",
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
    architecture_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("group.md", job, max_turns=15,
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
    architecture_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("holistic.md", job, max_turns=15, holistic_output="/tmp/h.md")
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
    architecture_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("self-review.md", job, max_turns=15, branch_name="feat")
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
    claude_md="", architecture_md="",
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("single-agent.md", job, max_turns=15)
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
    claude_md="", architecture_md="",
    omitted_files=["b.go"],
)
job = mod.ReviewJob(
    repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
    wt_path="/tmp/wt", review_file="/tmp/review.md",
    session_log="/tmp/session.jsonl", reviews_dir="/tmp/reviews",
    preflight=preflight,
)
result = mod.build_prompt("single-agent.md", job, max_turns=15)
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
result = mod.build_prompt("single-agent.md", job, max_turns=15)
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
    claude_md="", architecture_md="",
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
    claude_md="", architecture_md="",
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
    architecture_md="",
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

# ── Density-based file content skipping ──────────────────────────────────────

@test "collect_preflight_data: low-density large file omitted from contents" {
  repo="$TMPDIR/repo_density"
  mkdir -p "$repo"
  cd "$repo"
  git init -q && git checkout -b main -q
  git config user.email "test@test.com" && git config user.name "Test"
  git config commit.gpgsign false
  python3 -c "print('x = 1\n' * 2000)" > "$repo/big.py"
  git add . && git commit -q --no-verify -m "init"
  git remote add origin "$repo" && git fetch -q origin main
  git checkout -b feat -q
  # Append 2 lines to a 1000-line file (0.2% density)
  echo "new_line_1" >> "$repo/big.py"
  echo "new_line_2" >> "$repo/big.py"
  git add . && git commit -q --no-verify -m "small change"

  result=$(_py_here <<PYEOF
import io, contextlib
pr = mod.PRMetadata(
    title='t', body='', head='feat', base='main', head_sha='abc',
    additions=2, deletions=0, changed_files=1,
    files=[{'path': 'big.py', 'additions': 2, 'deletions': 0}],
)
ctx = mod.PRContext()
job = mod.ReviewJob(
    repo='r', pr_number='1', pr=pr, ctx=ctx,
    wt_path='$repo', review_file='/tmp/r.md',
    session_log='/tmp/s.jsonl', reviews_dir='/tmp/rev',
)
with contextlib.redirect_stdout(io.StringIO()):
    data = mod.collect_preflight_data(job)
in_contents = 'big.py' in data.file_contents
in_omitted = 'big.py' in data.omitted_files
print(f'contents={in_contents},omitted={in_omitted}')
PYEOF
)
  [ "$result" = "contents=False,omitted=True" ]
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
