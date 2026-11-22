# Testing

## Writing Tests

- Write tests the same way as existing tests in the project
- Tests are not complete until they run and all pass
- Never disable a test as a fix for a failing test
- A test failing because it reached for a tool the CI platform lacks is a portability bug in the test, not a case for a platform guard; read the subject portably
- When a foundational method's contract changes, audit every test that asserts the old behavior and update it
- Prefer real dependencies over mocks when feasible — mocks hide integration bugs
- Every bug fix and behavioral change must include a regression test. The self-review fix pass enforces this: a claimed fix without one is reported unverified (`self-review/SKILL.md` § The fix pass owes a test for every behaviour change)

## Reading a Suite Result

- Never pipe a test suite or validator into `tail`, `head`, `grep`, or any other
  filter to inspect its result. The pipeline's exit status is the filter's, not the
  suite's — `false | tail -1` exits 0 — so a failing run reads as a pass, and a
  `$?` captured after the pipe is reporting on the filter. Redirect to a file and
  read the status, then grep the file
- `set -o pipefail` makes the pipeline report the first failing stage, which is the
  one narrow way a pipe is safe here. Prefer the redirect anyway: the status is the
  thing being checked, and a file leaves the whole run to read afterwards
- Do not finish the command with `; echo $?` either. A trailing statement becomes
  the command's own exit status, so `npm test > out.txt 2>&1; echo "EXIT=$?"` exits
  0 whatever the suite did — the same masking as the pipe, one statement later. The
  printed line is truthful and a foreground caller reads it, which is what makes the
  habit feel safe
- It stops being cosmetic the moment the command is handed to a background job. The
  job facility reports the *process's* exit code as its pass/fail notice, so a
  masked status is announced as "succeeded" for a suite that failed, and the real
  result is in output nobody re-reads. Three such notices in one session are why
  this is written down. Let the runner be the last thing the command does and read
  the output afterwards; where a status must be captured mid-command, end with an
  honest `exit "$status"`
- Enforced by `ai/pi/extensions/test-pipe-guard` and
  `ai/pi/extensions/exit-status-guard` under Pi, and by
  `ai/claude/bin/claude-bash-guard` under Claude Code

## A Test Must Fail When Its Subject Breaks

A test that cannot fail is worse than no test. A passing test proves nothing until the subject is broken and the test is seen to fail.

- Break the line the test exists to hold, never the expected value, then run the whole test file, never a `-k`, `grep` or subset path that can deselect the test
- Never restore with `git checkout <file>`: it reverts the change under test too. Rewrite the file back, then `grep` for the line the test holds before believing the restored run
- Full procedure, fixture pitfalls and `check-new-tests`: `ai/skills/test-verification/SKILL.md` — read it when writing or verifying a test
- Do not add tests that simply assert constant values
- The assertion cannot be satisfied incidentally. An `or` arm that is always true (`assert x in content or " " in text`) makes the whole assertion a tautology, and it passes on any input. Two narrower forms recur: a bare `err != nil` (the test is named for one failure and passes on any other, including one thrown by a field it forgot to set before the branch was ever reached), and a membership check against the value's own enum or allow-list (which passes when two entries are swapped). Assert the sentinel, the error type, or the exact mapped value
- The patch target is the name the code under test actually looks up. Patching `mod.subprocess.run` proves nothing once the function was migrated to call `gh_client.api` — the test keeps passing against code it no longer touches
- The input reaches the branch under test rather than stopping at an early guard. A test named for a commit failure that supplies input rejected before the commit is attempted exercises the guard, not the failure
- Cover the path the change is actually on before the edge case beside it. A commit that reworks a common-path helper and adds a case only for non-ASCII input, an empty list, or a rare error has tested the thing it did not change; the common path it did change is still held by nothing
- Assert the exit status alongside the output. A test matching only stdout passes for a command that emitted the expected line and then failed
- Cover both unset and empty-string when testing a config or env-var fallback — real environments export `VAR=""`, and `get(k, default)` returns the empty string where `get(k) or default` returns the default
- Extracting code that two consumers share: pin the existing behaviour first with characterization tests that run against the unmodified code (they must pass before the move, which also proves the test is true), then move the code with that suite untouched. A value the plan or the author predicted that the real code disproves is a recorded ruling — the test follows the code, never the reverse. An intended behaviour change goes in its own commit that edits only the tests it names, so "no behaviour change" stays checkable everywhere else. Observed 2026-11: a 17-test characterization harness over a 600-line view module found one wrong predicted value and one guard (`!listingOnly || !prev?.repo`) that no test pinned until the store's own suite did.

## Test Isolation

- Keep test files inside the test's own `tmp_path` — a static filename in `tmp_path.parent` or `/tmp` is a race between tests sharing that directory
- A tracked file is never the scratch space either. A test that rewrites a real source file and restores it in `finally` leaves the working tree dirty on a hard kill, races any other test or worktree reading that file, and edits the thing under test rather than a copy of it. Copy it into `tmp_path` and point the tool at the copy
- In bats, never `rm -rf "$TMPDIR"` in teardown (a setup that failed early leaves it the machine's real temp dir) and never `mktemp -d`; bats makes and removes its own scratch
- A temp git repo in a fixture needs an identity (`-c user.name=... -c user.email=...` or `GIT_AUTHOR_*`), or its commits fail on any machine without a global git config
