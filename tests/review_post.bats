#!/usr/bin/env bats
# Tests for review-post Python script — finding parsing, diff classification,
# renumbering, and comment formatting.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  REVIEW_POST="$REPO_ROOT/ai/claude/bin/review-post"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: run Python expression importing from the review-post script
_py() {
  python3 -c "
import sys, importlib.util, importlib.machinery
loader = importlib.machinery.SourceFileLoader('rp', '$REVIEW_POST')
spec = importlib.util.spec_from_loader('rp', loader)
mod = importlib.util.module_from_spec(spec)
sys.modules['rp'] = mod
spec.loader.exec_module(mod)
$1
"
}

# Helper: create a Finding object
_mkfinding() {
  echo "mod.Finding(id='$1', severity='${1:0:1}', seq=${1:1}, path='${2:-}', line=${3:-None}, end_line=${4:-None}, body='${5:-}', full_path='${6:-}', posted_id='${7:-}', classification='${8:-}', skip_reason='${9:-}')"
}

# ── _extract_path ────────────────────────────────────────────────────────────

@test "_extract_path: bold+backtick format with line number" {
  result=$(_py "
path, line, end = mod._extract_path('**\`pkg/handler.go:42\`**')
print(f'{path}|{line}|{end}')
")
  [ "$result" = "pkg/handler.go|42|None" ]
}

@test "_extract_path: bold+backtick format with line range" {
  result=$(_py "
path, line, end = mod._extract_path('**\`pkg/handler.go:10-20\`**')
print(f'{path}|{line}|{end}')
")
  [ "$result" = "pkg/handler.go|10|20" ]
}

@test "_extract_path: bold-only format" {
  result=$(_py "
path, line, end = mod._extract_path('**pkg/handler.go:5**')
print(f'{path}|{line}|{end}')
")
  [ "$result" = "pkg/handler.go|5|None" ]
}

@test "_extract_path: backtick-only format" {
  result=$(_py "
path, line, end = mod._extract_path('\`pkg/handler.go:99\`')
print(f'{path}|{line}|{end}')
")
  [ "$result" = "pkg/handler.go|99|None" ]
}

@test "_extract_path: no line number" {
  result=$(_py "
path, line, end = mod._extract_path('**\`handler.go\`**')
print(f'{path}|{line}|{end}')
")
  [ "$result" = "handler.go|None|None" ]
}

@test "_extract_path: no path match returns empty" {
  result=$(_py "
path, line, end = mod._extract_path('some random text')
print(f'{path}|{line}|{end}')
")
  [ "$result" = "|None|None" ]
}

@test "_extract_path: en-dash range separator" {
  result=$(_py "
path, line, end = mod._extract_path('**\`file.go:10–15\`**')
print(f'{path}|{line}|{end}')
")
  [ "$result" = "file.go|10|15" ]
}

# ── _extract_body_text ───────────────────────────────────────────────────────

@test "_extract_body_text: em-dash separator" {
  result=$(_py "print(mod._extract_body_text('prefix — the body text'))")
  [ "$result" = "the body text" ]
}

@test "_extract_body_text: double-dash fallback" {
  result=$(_py "print(mod._extract_body_text('prefix -- the body text'))")
  [ "$result" = "the body text" ]
}

@test "_extract_body_text: hyphen fallback" {
  result=$(_py "print(mod._extract_body_text('prefix - the body text'))")
  [ "$result" = "the body text" ]
}

@test "_extract_body_text: no separator returns empty" {
  result=$(_py "print(repr(mod._extract_body_text('no separator here')))")
  [ "$result" = "''" ]
}

@test "_extract_body_text: em-dash takes precedence over double-dash" {
  result=$(_py "print(mod._extract_body_text('a -- b — c'))")
  [ "$result" = "c" ]
}

# ── _parse_finding_line ──────────────────────────────────────────────────────

@test "_parse_finding_line: standard must-fix finding" {
  result=$(_py "
f = mod._parse_finding_line('- **[M1]** **\`handler.go:42\`** — Fix this bug')
print(f'{f.id}|{f.severity}|{f.seq}|{f.path}|{f.line}|{f.body}')
")
  [ "$result" = "M1|M|1|handler.go|42|Fix this bug" ]
}

@test "_parse_finding_line: should-fix with line range" {
  result=$(_py "
f = mod._parse_finding_line('- **[S3]** **\`pkg/auth.go:10-20\`** — Refactor')
print(f'{f.id}|{f.severity}|{f.seq}|{f.line}|{f.end_line}')
")
  [ "$result" = "S3|S|3|10|20" ]
}

@test "_parse_finding_line: nit finding" {
  result=$(_py "
f = mod._parse_finding_line('- **[N2]** **\`file.go:5\`** — Nit text')
print(f'{f.severity}|{f.seq}')
")
  [ "$result" = "N|2" ]
}

@test "_parse_finding_line: idiom finding" {
  result=$(_py "
f = mod._parse_finding_line('- **[I1]** **\`file.go:5\`** — Use pattern')
print(f'{f.severity}|{f.seq}')
")
  [ "$result" = "I|1" ]
}

@test "_parse_finding_line: checkbox variant" {
  result=$(_py "
f = mod._parse_finding_line('- [ ] **[S1]** **\`file.go:5\`** — Fix this')
print(f'{f.id}|{f.body}')
")
  [ "$result" = "S1|Fix this" ]
}

@test "_parse_finding_line: non-finding line returns None" {
  result=$(_py "print(mod._parse_finding_line('just a regular line'))")
  [ "$result" = "None" ]
}

@test "_parse_finding_line: section header returns None" {
  result=$(_py "print(mod._parse_finding_line('## Must fix'))")
  [ "$result" = "None" ]
}

# ── parse_findings (core parser) ─────────────────────────────────────────────

@test "parse_findings: single finding single section" {
  result=$(_py "
text = '''## Must fix

- **[M1]** **\`file.go:10\`** — Fix the bug
'''
findings = mod.parse_findings(text)
print(len(findings))
print(findings[0].body)
")
  [[ "$result" == *"1"* ]]
  [[ "$result" == *"Fix the bug"* ]]
}

@test "parse_findings: multiple findings in one section" {
  result=$(_py "
text = '''## Must fix

- **[M1]** **\`a.go:1\`** — First finding
- **[M2]** **\`b.go:2\`** — Second finding
'''
findings = mod.parse_findings(text)
print(len(findings))
print(findings[0].id, findings[0].body)
print(findings[1].id, findings[1].body)
")
  [[ "$result" == *"2"* ]]
  [[ "$result" == *"M1 First finding"* ]]
  [[ "$result" == *"M2 Second finding"* ]]
}

@test "parse_findings: findings across multiple sections" {
  result=$(_py "
text = '''## Must fix

- **[M1]** **\`a.go:1\`** — Must body

## Should fix

- **[S1]** **\`b.go:2\`** — Should body

## Nit

- **[N1]** **\`c.go:3\`** — Nit body
'''
findings = mod.parse_findings(text)
print(len(findings))
for f in findings:
    print(f'{f.id}:{f.body}')
")
  [[ "$result" == *"3"* ]]
  [[ "$result" == *"M1:Must body"* ]]
  [[ "$result" == *"S1:Should body"* ]]
  [[ "$result" == *"N1:Nit body"* ]]
}

@test "parse_findings: last finding in section preserves body (the double-finalize bug)" {
  result=$(_py "
text = '''## Must fix

- **[M1]** **\`a.go:1\`** — First must-fix
- **[M2]** **\`b.go:2\`** — Last must-fix body

## Should fix

- **[S1]** **\`c.go:3\`** — First should-fix

## Nit

- **[N1]** **\`d.go:4\`** — The only nit

## Verdict

All done.
'''
findings = mod.parse_findings(text)
for f in findings:
    print(f'{f.id}|{len(f.body)}|{f.body[:30]}')
")
  # M2 is last in Must fix — was the bug trigger
  [[ "$result" == *"M2|"* ]]
  [[ "$result" != *"M2|0|"* ]]
  [[ "$result" == *"M2|"*"Last must-fix body"* ]]
  # S1 is last in Should fix — also affected
  [[ "$result" == *"S1|"*"First should-fix"* ]]
  [[ "$result" != *"S1|0|"* ]]
  # N1 is last in Nit, followed by non-severity section — also affected
  [[ "$result" == *"N1|"*"The only nit"* ]]
  [[ "$result" != *"N1|0|"* ]]
}

@test "parse_findings: multi-line finding with continuation lines" {
  result=$(_py "
text = '''## Must fix

- **[M1]** **\`file.go:10\`** — Main body text
  More details here.
  And another line.
'''
findings = mod.parse_findings(text)
print(findings[0].body)
")
  [[ "$result" == *"Main body text"* ]]
  [[ "$result" == *"More details here."* ]]
  [[ "$result" == *"And another line."* ]]
}

@test "parse_findings: multi-line finding with code block" {
  result=$(_py "
text = '''## Must fix

- **[M1]** **\`workflow.yml:10\`** — Add permissions block:
  \`\`\`yaml
  permissions:
    contents: read
  \`\`\`
  Note about the fix.
'''
findings = mod.parse_findings(text)
print(findings[0].body)
")
  [[ "$result" == *"Add permissions block:"* ]]
  [[ "$result" == *"permissions:"* ]]
  [[ "$result" == *"contents: read"* ]]
  [[ "$result" == *"Note about the fix."* ]]
}

@test "parse_findings: multi-line last-in-section preserves full body (regression test)" {
  result=$(_py "
text = '''## Must fix

- **[M1]** **\`a.go:1\`** — Simple finding

- **[M2]** **\`workflow.yml:50-60\`** — Complex finding.
  Code block follows:
  \`\`\`yaml
  jobs:
    build:
      runs-on: ubuntu
  \`\`\`
  *(Note referencing other comments.)*

## Should fix

- **[S1]** **\`b.go:5\`** — Should fix body
'''
findings = mod.parse_findings(text)
m2 = [f for f in findings if f.id == 'M2'][0]
print(len(m2.body))
print(m2.body[:40])
")
  # M2 body must not be empty (was the exact bug)
  [[ "$result" != *$'\n'"0"* ]]
  [[ "$result" == *"Complex finding"* ]]
}

@test "parse_findings: strikethrough findings are skipped" {
  result=$(_py "
text = '''## Must fix

- ~~**[M1]** **\`file.go:10\`** — Resolved~~
- **[M2]** **\`file.go:20\`** — Still open
'''
findings = mod.parse_findings(text)
print(len(findings))
print(findings[0].id)
")
  [[ "$result" == *"1"* ]]
  [[ "$result" == *"M2"* ]]
}

@test "parse_findings: sub-headers handled" {
  result=$(_py "
text = '''## Must fix

### Group A

- **[M1]** **\`a.go:1\`** — First

### Group B

- **[M2]** **\`b.go:2\`** — Second
'''
findings = mod.parse_findings(text)
print(len(findings))
print(findings[0].body)
print(findings[1].body)
")
  [[ "$result" == *"2"* ]]
  [[ "$result" == *"First"* ]]
  [[ "$result" == *"Second"* ]]
}

@test "parse_findings: non-severity section stops parsing" {
  result=$(_py "
text = '''## Must fix

- **[M1]** **\`a.go:1\`** — A finding

## Verdict

This is not a finding: - **[M2]** **\`b.go:2\`** — Not parsed
'''
findings = mod.parse_findings(text)
print(len(findings))
")
  [[ "$result" == *"1"* ]]
}

@test "parse_findings: empty input returns empty list" {
  result=$(_py "print(len(mod.parse_findings('')))")
  [ "$result" = "0" ]
}

@test "parse_findings: no severity sections returns empty list" {
  result=$(_py "
text = '''# Review

Some preamble text.

## Summary

Nothing here.
'''
print(len(mod.parse_findings(text)))
")
  [ "$result" = "0" ]
}

@test "parse_findings: finding with no body text" {
  result=$(_py "
text = '''## Nit

- **[N1]** **\`file.go:10\`**
'''
findings = mod.parse_findings(text)
print(repr(findings[0].body))
")
  [ "$result" = "''" ]
}

# ── parse_diff_hunks ─────────────────────────────────────────────────────────

@test "parse_diff_hunks: single file single hunk" {
  result=$(_py "
diff = '''diff --git a/file.go b/file.go
--- a/file.go
+++ b/file.go
@@ -10,5 +10,7 @@ func main() {
+new line
'''
hunks = mod.parse_diff_hunks(diff)
print(list(hunks.keys()))
print(hunks['file.go'])
")
  [[ "$result" == *"file.go"* ]]
  [[ "$result" == *"(10, 16)"* ]]
}

@test "parse_diff_hunks: multiple files" {
  result=$(_py "
diff = '''diff --git a/a.go b/a.go
--- a/a.go
+++ b/a.go
@@ -1,3 +1,5 @@
+line
diff --git a/b.go b/b.go
--- a/b.go
+++ b/b.go
@@ -10,2 +10,4 @@
+line
'''
hunks = mod.parse_diff_hunks(diff)
print(sorted(hunks.keys()))
")
  [[ "$result" == *"a.go"* ]]
  [[ "$result" == *"b.go"* ]]
}

@test "parse_diff_hunks: multiple hunks per file" {
  result=$(_py "
diff = '''diff --git a/file.go b/file.go
--- a/file.go
+++ b/file.go
@@ -1,3 +1,5 @@
+line
@@ -20,3 +22,5 @@
+line
'''
hunks = mod.parse_diff_hunks(diff)
print(len(hunks['file.go']))
print(hunks['file.go'])
")
  [[ "$result" == *"2"* ]]
  [[ "$result" == *"(1, 5)"* ]]
  [[ "$result" == *"(22, 26)"* ]]
}

@test "parse_diff_hunks: hunk with length 1 (omitted count)" {
  result=$(_py "
diff = '''diff --git a/file.go b/file.go
--- a/file.go
+++ b/file.go
@@ -5,3 +5 @@
-removed
'''
hunks = mod.parse_diff_hunks(diff)
print(hunks['file.go'])
")
  [[ "$result" == *"(5, 5)"* ]]
}

@test "parse_diff_hunks: new file (all added)" {
  result=$(_py "
diff = '''diff --git a/new.go b/new.go
new file mode 100644
--- /dev/null
+++ b/new.go
@@ -0,0 +1,20 @@
+package main
'''
hunks = mod.parse_diff_hunks(diff)
print(hunks['new.go'])
")
  [[ "$result" == *"(1, 20)"* ]]
}

# ── _resolve_path ────────────────────────────────────────────────────────────

@test "_resolve_path: exact match" {
  result=$(_py "
hunks = {'pkg/handler.go': [(1, 10)]}
print(mod._resolve_path('pkg/handler.go', hunks))
")
  [ "$result" = "pkg/handler.go" ]
}

@test "_resolve_path: basename match unique" {
  result=$(_py "
hunks = {'pkg/handler.go': [(1, 10)]}
print(mod._resolve_path('handler.go', hunks))
")
  [ "$result" = "pkg/handler.go" ]
}

@test "_resolve_path: basename match ambiguous returns None" {
  result=$(_py "
hunks = {'pkg/a/handler.go': [(1, 10)], 'pkg/b/handler.go': [(1, 10)]}
print(mod._resolve_path('handler.go', hunks))
")
  [ "$result" = "None" ]
}

@test "_resolve_path: partial path match" {
  result=$(_py "
hunks = {'src/pkg/service/handler.go': [(1, 10)]}
print(mod._resolve_path('service/handler.go', hunks))
")
  [ "$result" = "src/pkg/service/handler.go" ]
}

@test "_resolve_path: no match returns None" {
  result=$(_py "
hunks = {'pkg/handler.go': [(1, 10)]}
print(mod._resolve_path('other.go', hunks))
")
  [ "$result" = "None" ]
}

# ── classify_findings ────────────────────────────────────────────────────────

@test "classify_findings: inline when path and line in hunk" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='file.go', line=5, end_line=None, body='x')
diff = '''diff --git a/file.go b/file.go
--- a/file.go
+++ b/file.go
@@ -1,3 +1,10 @@
+line
'''
inline, fl, skipped = mod.classify_findings([f], diff)
print(len(inline), len(fl), len(skipped))
print(inline[0].classification)
")
  [[ "$result" == *"1 0 0"* ]]
  [[ "$result" == *"inline"* ]]
}

@test "classify_findings: file-level when line not in hunk" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='file.go', line=50, end_line=None, body='x')
diff = '''diff --git a/file.go b/file.go
--- a/file.go
+++ b/file.go
@@ -1,3 +1,10 @@
+line
'''
inline, fl, skipped = mod.classify_findings([f], diff)
print(len(inline), len(fl), len(skipped))
print(fl[0].classification)
print(fl[0].skip_reason)
")
  [[ "$result" == *"0 1 0"* ]]
  [[ "$result" == *"file_level"* ]]
  [[ "$result" == *"not in any diff hunk"* ]]
}

@test "classify_findings: file-level when no line number" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='file.go', line=None, end_line=None, body='x')
diff = '''diff --git a/file.go b/file.go
--- a/file.go
+++ b/file.go
@@ -1,3 +1,10 @@
+line
'''
inline, fl, skipped = mod.classify_findings([f], diff)
print(len(inline), len(fl), len(skipped))
print(fl[0].skip_reason)
")
  [[ "$result" == *"0 1 0"* ]]
  [[ "$result" == *"no line number"* ]]
}

@test "classify_findings: skipped when path not in diff" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='other.go', line=5, end_line=None, body='x')
diff = '''diff --git a/file.go b/file.go
--- a/file.go
+++ b/file.go
@@ -1,3 +1,10 @@
+line
'''
inline, fl, skipped = mod.classify_findings([f], diff)
print(len(inline), len(fl), len(skipped))
print(skipped[0].classification)
")
  [[ "$result" == *"0 0 1"* ]]
  [[ "$result" == *"skipped"* ]]
}

@test "classify_findings: resolves bare filename to full path" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='handler.go', line=5, end_line=None, body='x')
diff = '''diff --git a/pkg/handler.go b/pkg/handler.go
--- a/pkg/handler.go
+++ b/pkg/handler.go
@@ -1,3 +1,10 @@
+line
'''
inline, fl, skipped = mod.classify_findings([f], diff)
print(len(inline))
print(inline[0].full_path)
")
  [[ "$result" == *"1"* ]]
  [[ "$result" == *"pkg/handler.go"* ]]
}

# ── renumber_for_posting ─────────────────────────────────────────────────────

@test "renumber_for_posting: inline sorted by path then line" {
  result=$(_py "
f1 = mod.Finding(id='M1', severity='M', seq=1, path='b.go', line=10, end_line=None, body='x', full_path='b.go')
f2 = mod.Finding(id='M2', severity='M', seq=2, path='a.go', line=5, end_line=None, body='y', full_path='a.go')
inline, body = mod.renumber_for_posting([f1, f2], [])
print(inline[0].posted_id, inline[0].full_path)
print(inline[1].posted_id, inline[1].full_path)
")
  # a.go comes before b.go — so M2 gets posted_id M1
  [[ "$result" == *"M1 a.go"* ]]
  [[ "$result" == *"M2 b.go"* ]]
}

@test "renumber_for_posting: body continues after inline" {
  result=$(_py "
fi = mod.Finding(id='S1', severity='S', seq=1, path='a.go', line=5, end_line=None, body='x', full_path='a.go')
fb = mod.Finding(id='S2', severity='S', seq=2, path='b.go', line=None, end_line=None, body='y', full_path='b.go')
inline, body = mod.renumber_for_posting([fi], [fb])
print(inline[0].posted_id)
print(body[0].posted_id)
")
  [[ "$result" == *"S1"* ]]
  [[ "$result" == *"S2"* ]]
}

@test "renumber_for_posting: independent counters per severity" {
  result=$(_py "
f1 = mod.Finding(id='M1', severity='M', seq=1, path='a.go', line=1, end_line=None, body='x', full_path='a.go')
f2 = mod.Finding(id='S1', severity='S', seq=1, path='a.go', line=2, end_line=None, body='y', full_path='a.go')
f3 = mod.Finding(id='M2', severity='M', seq=2, path='a.go', line=3, end_line=None, body='z', full_path='a.go')
inline, _ = mod.renumber_for_posting([f1, f2, f3], [])
print(inline[0].posted_id)
print(inline[1].posted_id)
print(inline[2].posted_id)
")
  [[ "$result" == *"M1"* ]]
  [[ "$result" == *"S1"* ]]
  [[ "$result" == *"M2"* ]]
}

# ── format_inline_comment ────────────────────────────────────────────────────

@test "format_inline_comment: single line" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='file.go', line=42, end_line=None, body='Fix bug', full_path='pkg/file.go', posted_id='M1')
c = mod.format_inline_comment(f)
print(c['path'])
print(c['line'])
print(c['body'])
print(c['side'])
print('start_line' in c)
")
  [[ "$result" == *"pkg/file.go"* ]]
  [[ "$result" == *"42"* ]]
  [[ "$result" == *"**[M1] [must-fix]** Fix bug"* ]]
  [[ "$result" == *"RIGHT"* ]]
  [[ "$result" == *"False"* ]]
}

@test "format_inline_comment: multi-line range" {
  result=$(_py "
f = mod.Finding(id='S1', severity='S', seq=1, path='file.go', line=10, end_line=20, body='Refactor', full_path='pkg/file.go', posted_id='S1')
c = mod.format_inline_comment(f)
print(c['start_line'])
print(c['line'])
print(c.get('start_side'))
")
  [[ "$result" == *"10"* ]]
  [[ "$result" == *"20"* ]]
  [[ "$result" == *"RIGHT"* ]]
}

@test "format_inline_comment: severity labels correct" {
  for sev_id in "M1|M|must-fix" "S1|S|should-fix" "N1|N|nit" "I1|I|idiom"; do
    IFS='|' read -r id sev label <<< "$sev_id"
    result=$(_py "
f = mod.Finding(id='${id}', severity='${sev}', seq=1, path='f.go', line=1, end_line=None, body='text', full_path='f.go', posted_id='${id}')
print(mod.format_inline_comment(f)['body'])
")
    [[ "$result" == *"[${label}]"* ]]
  done
}

# ── format_body_text ─────────────────────────────────────────────────────────

@test "format_body_text: with inline comments shows 'Have some comments'" {
  result=$(_py "print(mod.format_body_text([], has_inline=True, severity_filter={'M', 'S', 'N'}))")
  [[ "$result" == *"Have some comments"* ]]
}

@test "format_body_text: without inline shows 'Review findings'" {
  result=$(_py "print(mod.format_body_text([], has_inline=False, severity_filter={'M', 'S', 'N'}))")
  [[ "$result" == *"Review findings"* ]]
}

@test "format_body_text: body findings listed with path refs" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='handler.go', line=42, end_line=None, body='Fix it', full_path='pkg/handler.go', posted_id='M1')
print(mod.format_body_text([f], has_inline=True, severity_filter={'M'}))
")
  [[ "$result" == *"**[M1] [must-fix]**"* ]]
  [[ "$result" == *"\`handler.go:42\`"* ]]
  [[ "$result" == *"Fix it"* ]]
}

@test "format_body_text: no body findings no extra content" {
  result=$(_py "
text = mod.format_body_text([], has_inline=True, severity_filter={'M', 'S'})
lines = text.strip().split('\n')
print(len(lines))
")
  [ "$result" = "1" ]
}

# ── _format_path_ref ─────────────────────────────────────────────────────────

@test "_format_path_ref: path with line" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='file.go', line=42, end_line=None, body='')
print(mod._format_path_ref(f))
")
  [ "$result" = '`file.go:42`' ]
}

@test "_format_path_ref: path with line range" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='file.go', line=10, end_line=20, body='')
print(mod._format_path_ref(f))
")
  [ "$result" = '`file.go:10-20`' ]
}

@test "_format_path_ref: path only" {
  result=$(_py "
f = mod.Finding(id='M1', severity='M', seq=1, path='file.go', line=None, end_line=None, body='')
print(mod._format_path_ref(f))
")
  [ "$result" = '`file.go`' ]
}

# ── End-to-end: parse_findings with real-world patterns ──────────────────────

@test "parse_findings: real-world review with all severity sections and code blocks" {
  cat > "$TMPDIR/review.md" << 'REVIEW'
# Review: org/repo#42

## Must fix

- **[M1]** **`workflow.yml:10-20`** — Missing permissions block.
  ```yaml
  permissions:
    contents: read
  ```

- **[M2]** **`handler.go:50`** — Race condition in handler.

## Should fix

- **[S1]** **`config.sh:30`** — Hardcoded prefix.

## Nit

- **[N1]** **`test.sh:5`** — Tests not in CI.

## Verdict

Request changes.
REVIEW
  result=$(_py "
text = open('$TMPDIR/review.md').read()
findings = mod.parse_findings(text)
for f in findings:
    has_body = 'YES' if f.body else 'NO'
    print(f'{f.id}|{has_body}|{f.body[:30]}')
")
  [[ "$result" == *"M1|YES|Missing permissions block."* ]]
  [[ "$result" == *"M2|YES|Race condition in handler."* ]]
  [[ "$result" == *"S1|YES|Hardcoded prefix."* ]]
  [[ "$result" == *"N1|YES|Tests not in CI."* ]]
}

@test "parse_findings: every last-in-section finding retains body across all section types" {
  cat > "$TMPDIR/review.md" << 'REVIEW'
## Must fix

- **[M1]** **`a.go:1`** — Must-fix alpha
- **[M2]** **`b.go:2`** — Must-fix beta (last in section)

## Should fix

- **[S1]** **`c.go:3`** — Should-fix only (last in section)

## Nit

- **[N1]** **`d.go:4`** — Nit alpha
- **[N2]** **`e.go:5`** — Nit beta (last in section)

## Verdict

Done.
REVIEW
  result=$(_py "
text = open('$TMPDIR/review.md').read()
findings = mod.parse_findings(text)
empty = [f.id for f in findings if not f.body]
print(f'empty_count={len(empty)}')
print(f'empty_ids={empty}')
for f in findings:
    print(f'{f.id}={f.body}')
")
  [[ "$result" == *"empty_count=0"* ]]
  [[ "$result" == *"M2=Must-fix beta (last in section)"* ]]
  [[ "$result" == *"S1=Should-fix only (last in section)"* ]]
  [[ "$result" == *"N2=Nit beta (last in section)"* ]]
}
