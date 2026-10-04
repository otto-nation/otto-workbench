#!/usr/bin/env bats
# Tests for the Claude bash guard: redirects, backgrounding, sleeping, variable expansion, and the guard-rules contract.
setup_file() {
  load 'test_helper'
  load 'claude_settings_helper'
  local repo_root
  repo_root="$(cd "$(dirname "$BATS_TEST_FILENAME")/.." && pwd)"

  # shellcheck source=/dev/null
  source "$repo_root/lib/registries.sh"

  # Collect registry permissions once for all tests
  local -a perms=()
  collect_registry_permissions perms "$repo_root"
  printf '%s\n' "${perms[@]}" > "$BATS_FILE_TMPDIR/registry_perms.list"

  _sync_settings_into "$BATS_FILE_TMPDIR/home" "$repo_root"
}

setup() {
  load 'test_helper'
  load 'claude_settings_helper'
  common_setup
}

teardown() {
  common_teardown
}

# ── file-writing redirects ──────────────────────────────────────────────────
# A Bash redirect is gated per write path, while the Edit and Write tools are
# allow-listed outright — so this rule steers to a different tool, not a
# different command. Only echo and printf are matched: a redirect capturing
# another command's output has no tool equivalent and must keep working.

@test "write hook: blocks printf appending to a repo file" {
  run _run_guard '{"tool_input":{"command":"printf -- '"'"'-- regen\\n'"'"' >> /Users/me/git/svc/schema/.latest.sql"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Write tool"* ]]
  [[ "$output" == *".latest.sql"* ]]
}

@test "write hook: blocks echo truncating a relative path" {
  run _run_guard '{"tool_input":{"command":"echo hello > notes.md"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Edit tool"* ]]
}

@test "write hook: blocks a redirect after a leading statement" {
  run _run_guard '{"tool_input":{"command":"ls; printf x >> notes.md"}}'
  [ "$status" -eq 2 ]
}

@test "write hook: allows a redirect to /dev/null" {
  run _run_guard '{"tool_input":{"command":"echo probe > /dev/null"}}'
  [ "$status" -eq 0 ]
}

@test "write hook: allows a redirect to a scratch path" {
  run _run_guard '{"tool_input":{"command":"printf ok >> /tmp/probe.log"}}'
  [ "$status" -eq 0 ]
}

@test "write hook: allows capturing another command's output" {
  run _run_guard '{"tool_input":{"command":"jq -r .name /Users/me/pkg.json > /Users/me/out.txt"}}'
  [ "$status" -eq 0 ]
}

@test "write hook: blocks a quoted destination path" {
  run _run_guard "{\"tool_input\":{\"command\":\"echo hi > '/Users/me/my notes.md'\"}}"
  [ "$status" -eq 2 ]
  [[ "$output" == *"a quoted path"* ]]
}

@test "write hook: allows a quoted argument before a scratch destination" {
  run _run_guard '{"tool_input":{"command":"echo \"hello world\" > /tmp/probe.log"}}'
  [ "$status" -eq 0 ]
}

@test "write hook: allows stderr redirection with no file target" {
  run _run_guard '{"tool_input":{"command":"echo probe 2>&1 | head -1"}}'
  [ "$status" -eq 0 ]
}

# ── backgrounding ───────────────────────────────────────────────────────────
# A `&` that stands alone backgrounds a shell the tool cannot reach, so the
# cleanup is a pkill. The neighbours matter: `&&`, `2>&1`, `&>` and `|&` all
# contain an ampersand and none of them background anything.

@test "background hook: blocks a trailing &" {
  run _run_guard '{"tool_input":{"command":"npm --prefix site run dev &"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"run_in_background"* ]]
}

@test "background hook: blocks a background start followed by a probe" {
  run _run_guard '{"tool_input":{"command":"python3 -m http.server 8931 & sleep 2; curl -sI localhost:8931"}}'
  [ "$status" -eq 2 ]
}

@test "background hook: blocks a background start on its own line" {
  run _run_guard '{"tool_input":{"command":"ignore/serve.py &\ncurl -sI localhost:8000"}}'
  [ "$status" -eq 2 ]
}

@test "background hook: blocks nohup" {
  run _run_guard '{"tool_input":{"command":"nohup node serve-out.mjs"}}'
  [ "$status" -eq 2 ]
}

@test "background hook: allows a && conjunction" {
  run _run_guard '{"tool_input":{"command":"git fetch origin main && git status"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows a 2>&1 redirect" {
  # The subject is the `&` in `2>&1`, which must not read as a background start.
  # Deliberately not a test runner: a piped suite is refused by the testpipe rule
  # below, which would make this pass or fail for the wrong reason.
  run _run_guard '{"tool_input":{"command":"make build 2>&1 | tail -5"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows a &> redirect" {
  run _run_guard '{"tool_input":{"command":"pytest tests/ &> /tmp/out.log"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows a |& pipe" {
  run _run_guard '{"tool_input":{"command":"shellcheck bin/local/validate-all |& head -20"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows a case statement fallthrough" {
  run _run_guard '{"tool_input":{"command":"case release in r*) echo tag ;& *) echo done ;; esac"}}'
  [ "$status" -eq 0 ]
}

@test "background hook: allows an ampersand inside a quoted argument" {
  run _run_guard "{\"tool_input\":{\"command\":\"grep -n 'a & b' /tmp/probe.txt\"}}"
  [ "$status" -eq 0 ]
}

# ── sleeping to wait ────────────────────────────────────────────────────────
# Unlike the rules above this one costs wall-clock, not a permission click: a
# `sleep 295` in front of work that reports its own completion spends five
# minutes to learn what the report says for free. The threshold is what keeps a
# real pipeline step — a second letting a server bind its port — out of it.

@test "sleep hook: blocks a long sleep waiting on a background job" {
  run _run_guard '{"tool_input":{"command":"sleep 295; echo done"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"reports its own completion"* ]]
}

@test "sleep hook: blocks a sleep exactly at the threshold" {
  # Also the whole-command case: a lone `sleep 10` is a single statement with no
  # separator, and the scan drops a final line with no terminator unless the
  # split adds one — which made every single-statement command invisible.
  run _run_guard '{"tool_input":{"command":"sleep 10"}}'
  [ "$status" -eq 2 ]
}

@test "sleep hook: blocks a long sleep behind a short one" {
  # The bypass a leftmost-match test leaves open: `=~` returns the first match
  # only, so a one-token `sleep 2 &&` in front waved the real wait through.
  run _run_guard '{"tool_input":{"command":"curl -sI localhost:8931; sleep 2 && sleep 300"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"300s"* ]]
}

@test "sleep hook: names the duration it refused" {
  # The message quotes the number back so the refusal reads as being about this
  # command rather than about sleeping in general.
  run _run_guard '{"tool_input":{"command":"sleep 120"}}'
  [[ "$output" == *"120s"* ]]
}

@test "sleep hook: blocks a long sleep after another statement" {
  # Statement-anchored like the rules above it: a leading no-op must not be a
  # way around the check.
  run _run_guard '{"tool_input":{"command":"gh pr checks 1257; sleep 60"}}'
  [ "$status" -eq 2 ]
}

@test "sleep hook: blocks a long sleep on its own line" {
  run _run_guard '{"tool_input":{"command":"gh pr checks 1257\nsleep 60\ngh pr checks 1257"}}'
  [ "$status" -eq 2 ]
}

@test "sleep hook: allows a short settle before a probe" {
  # The case the threshold exists for. This one is still blocked — by the
  # backgrounding rule, which owns the `&` — so the sleep is tested on its own.
  run _run_guard '{"tool_input":{"command":"sleep 2; curl -sI localhost:8931"}}'
  [ "$status" -eq 0 ]
}

@test "sleep hook: reads a zero-padded duration as base ten" {
  # Bash reads a leading zero as octal, so an unprefixed comparison aborts on
  # `sleep 08` with "value too great for base" and the command goes through on an
  # error rather than a decision. Both sides of the threshold are checked, since a
  # guard that errored would let the blocking case past too.
  run _run_guard '{"tool_input":{"command":"sleep 08"}}'
  [ "$status" -eq 0 ]
  run _run_guard '{"tool_input":{"command":"sleep 060"}}'
  [ "$status" -eq 2 ]
}

@test "sleep hook: allows a fractional sleep" {
  # Under the threshold by definition, and the integer match never sees it.
  run _run_guard '{"tool_input":{"command":"sleep 0.5; curl -sI localhost:8931"}}'
  [ "$status" -eq 0 ]
}

@test "sleep hook: allows a --sleep flag on another command" {
  run _run_guard '{"tool_input":{"command":"pr ci --wait --sleep 30"}}'
  [ "$status" -eq 0 ]
}

@test "sleep hook: allows sleep as a grep pattern" {
  run _run_guard "{\"tool_input\":{\"command\":\"grep -rn 'sleep 300' bin/local\"}}"
  [ "$status" -eq 0 ]
}

@test "sleep hook: allows a sleep inside a heredoc body" {
  # Content being written to a file, not a command being run — the same
  # exemption every statement-anchored rule above gets.
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/poll.sh <<EOF\nsleep 300\nEOF"}}'
  [ "$status" -eq 0 ]
}

# ── shell variable expansion ────────────────────────────────────────────────
# A `$VAR` reference is flagged as "simple_expansion" and prompts every time.
# This rule reads text stripped of single-quoted spans only: `'$HOME'` is a
# literal, `"$f"` expands, so only the latter may be mistaken for one.

@test "expand hook: blocks a for loop over a file list" {
  run _run_guard '{"tool_input":{"command":"for f in a.sql b.sql; do echo \"### $f\"; rtk read \"$f\"; done"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"simple_expansion"* ]]
  [[ "$output" == *"tail -n +1"* ]]
}

@test "expand hook: blocks a bare \$VAR outside quotes" {
  run _run_guard '{"tool_input":{"command":"echo $HOME"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"printenv"* ]]
}

@test "expand hook: blocks the \${VAR} brace form" {
  run _run_guard '{"tool_input":{"command":"ls ${HOME}/git"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"simple_expansion"* ]]
}

@test "expand hook: allows a \$VAR inside single quotes" {
  run _run_guard "{\"tool_input\":{\"command\":\"grep -n '\\\$HOME' /tmp/x/f\"}}"
  [ "$status" -eq 0 ]
}

@test "expand hook: allows a perl one-liner whose regex ends in \$" {
  run _run_guard "{\"tool_input\":{\"command\":\"perl -0pi -e 's/foo\\\$/bar/' /tmp/x/f\"}}"
  [ "$status" -eq 0 ]
}

@test "expand hook: allows a \$VAR inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\necho \"$HOME\"\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "expand hook: allows a command with no expansion" {
  run _run_guard '{"tool_input":{"command":"tail -n +1 a.sql b.sql c.sql"}}'
  [ "$status" -eq 0 ]
}

@test "expand hook: defers to the compound cd rule" {
  run _run_guard '{"tool_input":{"command":"cd /tmp/x && echo \"$HOME\""}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Compound cd"* ]]
}

@test "cd hook: blocks a compound cd after a leading token" {
  run _run_guard '{"tool_input":{"command":"mkdir -p /tmp/x; cd /tmp/x && ls"}}'
  [ "$status" -eq 2 ]
}

@test "cd hook: allows a cd nested inside a quoted argument" {
  run _run_guard "{\"tool_input\":{\"command\":\"grep -rn 'cd /tmp/x && ls' /tmp/x; true\"}}"
  [ "$status" -eq 0 ]
}

@test "env hook: blocks env -C" {
  run _run_guard '{"tool_input":{"command":"env -C /tmp/wt pytest tests/foo_test.py -q"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "env hook: blocks env --chdir" {
  run _run_guard '{"tool_input":{"command":"env --chdir=/tmp/wt bats tests/"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "env hook: blocks env -C after a pipe" {
  run _run_guard '{"tool_input":{"command":"echo hi | env -C /tmp/wt cat"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"BLOCKED"* ]]
}

@test "env hook: allows env without a directory flag" {
  run _run_guard '{"tool_input":{"command":"env | grep PATH"}}'
  [ "$status" -eq 0 ]
}

@test "env hook: blocks env -C behind another flag" {
  run _run_guard '{"tool_input":{"command":"env -i -C /tmp/wt pytest tests/"}}'
  [ "$status" -eq 2 ]
}

@test "env hook: blocks env -C with an attached directory" {
  run _run_guard '{"tool_input":{"command":"env -C/tmp/wt ls"}}'
  [ "$status" -eq 2 ]
}

@test "env hook: allows an env var assignment passed to env" {
  run _run_guard '{"tool_input":{"command":"env FOO=bar printenv FOO"}}'
  [ "$status" -eq 0 ]
}

# ── Guard ↔ rules contract ───────────────────────────────────────────────────
# A block message must name the alternative and cite the rules section holding
# the rest. A rule blocking Claude with no documented alternative is worse than
# the prompt it prevents, so both halves of the citation are tested: that every
# block call carries one, and that every one it carries resolves to a heading.

@test "guard: every block call cites a rules section" {
  local guard="$REPO_ROOT/ai/claude/bin/claude-bash-guard"
  local uncited
  uncited=$(grep -n '^  block ' "$guard" | grep -v 'See [a-z-]*\.md §' || true)
  [ -z "$uncited" ] || {
    echo "block call(s) with no 'See <doc>.md § <Section>' citation:"
    echo "$uncited"
    return 1
  }
}

@test "guard: every cited rules section exists" {
  local guard="$REPO_ROOT/ai/claude/bin/claude-bash-guard"
  local doc section
  # Headings are compared with backticks stripped: the guard's messages are
  # plain text, so `## Avoid \`env -C\`` is cited as `§ Avoid env -C`.
  while IFS='|' read -r doc section; do
    [ -f "$REPO_ROOT/ai/guidelines/rules/$doc" ] || {
      echo "guard cites a rules file that does not exist: $doc"
      return 1
    }
    sed 's/`//g' "$REPO_ROOT/ai/guidelines/rules/$doc" | grep -qxF "## $section" || {
      echo "guard cites '$doc § $section', which has no matching heading"
      return 1
    }
  done < <(grep -oE 'See [a-z-]+\.md § [^.]+\.' "$guard" |
    sed -E 's/^See ([a-z-]+\.md) § (.*)\.$/\1|\2/' | sort -u)
}

# The bodies below each put the pattern after a statement separator, which is
# what the whole-command form matched on — a body line starting with the pattern
# was never a false positive, since the regex only anchors to start-of-string.

@test "funcdef hook: allows a function definition inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/lib.sh <<EOF\nsetup; run() { echo hi; }\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "var hook: allows an assignment inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\ntrue; FOO=bar\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "reposcript hook: allows an absolute bin/local path inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\ntrue; /Users/me/repo/bin/local/validate-all\nEOF"}}'
  [ "$status" -eq 0 ]
}

@test "cd hook: allows a compound cd inside a heredoc body" {
  run _run_guard '{"tool_input":{"command":"cat > /tmp/x/run.sh <<EOF\nmkdir -p /tmp/y; cd /tmp/y && ls\nEOF"}}'
  [ "$status" -eq 0 ]
}

# ── whole-command Bash guardrails ───────────────────────────────────────────
# These scan the entire command rather than the quote-stripped first line: the
# analyzer flags them wherever they appear, including inside a quoted argument.

@test "exec hook: blocks find -exec" {
  run _run_guard '{"tool_input":{"command":"find . -name x -exec grep foo {} ;"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"-print0"* ]]
}

@test "subst hook: blocks command substitution" {
  run _run_guard '{"tool_input":{"command":"ls $(git rev-parse --show-toplevel)"}}'
  [ "$status" -eq 2 ]
  [[ "$output" == *"Run the inner command first"* ]]
}
