---
name: test-verification
description: "Proving a test holds its subject: the revert check, fixtures that mask a condition, restoring a reverted file safely, pinning conditional paths, check-new-tests and the passes-at-base marker, and hand-run drills. TRIGGER when: writing, reviewing or verifying a test, running a revert check, or adding a regression test for a fix. SKIP: reading a suite result (stays in testing.md); running a suite with no new or changed test."
source: otto-workbench/ai/skills/test-verification/SKILL.md
kind: rule
invocation: "/test-verification"
trigger: "Use when writing, reviewing or verifying a test, running a revert check, or adding a regression test for a fix or behaviour change."
skip: "Do not use for reading a suite result, which stays in testing.md, or for running a suite when no test was added or changed."
---

## A Test Must Fail When Its Subject Breaks

A test that cannot fail is worse than no test: it reports the behavior is held
when nothing holds it. Before believing a passing test, check all three.

The check that settles it is to break the subject and watch the test fail. Delete
the line the test exists to hold, run it, confirm it fails, restore. A passing
test proves the code passes; only a failing one proves the test is holding
anything. Every item below is a way a test can look right and hold nothing, and
the revert catches all of them at once — including the ways not listed.

Do this hardest on a test written to close a review finding. The recurring
failure is not a wrong fix but a correct fix with a test that does not constrain
it: a fixture that stubs out the very function the fix changed, an assertion on
what the caller passed rather than on what the callee did with it. The fix is
right, the test passes, and deleting the fix leaves it passing.

A fixture that breaks two rules masks both. A refusal test whose input violates
the condition under test *and* a neighbouring one passes with the first condition
deleted, because the second still refuses it. Observed 2026-11 (opus plan, grok
implementers through pi's `subagent` tool, an Ansible role's validation assert):
deleting the `cpu_quota` condition left its refusal test green because the same
fixture also failed the `docker_cpu_quota` condition beside it. The plan's
prediction of which test each deletion would fail was wrong twice, once for this
reason. Give each condition a fixture whose only violation is that one, and read
the revert output rather than trusting the prediction.

A filter is the other way to fool yourself here: `-k`, a `grep`, a subset path.
A revert whose test was deselected reports no failures and reads as proof. Run
the whole file.

Restoring the file is part of the revert, and it is where the experiment most
easily destroys what it was testing. Never restore with `git checkout <file>`:
it reverts the change under test along with the experiment, and the run that
follows is green because the feature is gone rather than because the test is
weak. Rewrite the file back to what it was instead, and confirm the change is
still there — `grep` for the line the test exists to hold — before believing
the restored run.

When a revert result contradicts the source in front of you, suspect the
experiment before the toolchain. A sequence of reverts and restores leaves the
tree in a state nobody is tracking, and the likeliest explanation for "the
source says X and the run says Y" is that the file was not what you thought
when the run started. `git diff` and `git log -1 --format=%ci` on the file
answer it; a bytecode cache almost never does. CPython invalidates a
`__pycache__` entry on any mtime mismatch, so an ordinary edit-and-restore
recompiles — the one shape that does slip through is a write landing on both
the same mtime *and* the same size as the cached source, which a real edit
rarely produces.

Revert the line that was actually wrong, which is not always the line the test
names, and never the expectation: a revert check that edits the expected value
only proves the assertion compares; the revert that counts breaks the subject
line and restores it by rewriting that line. A helper can be correct and its
caller defeat it: a cache read that a caller already invalidated, a flag the
caller never passes, a guard the caller skips. Reverting the helper fails the
helper's test and proves nothing about the wiring, so the bug ships under a
green suite. When a change adds a function
*and* a call to it, the revert that settles it is at the call site — delete the
argument, restore the old call, put the wrong order back — and a test that
survives that is testing the unit, not the behaviour.

A test whose subject is a periodic or conditional path must pin the condition
that selects it. Shared counters, clocks and module state decide which branch a
test exercises, and the default is rarely the one it is named for: a tick
counter fresh at `0` takes the every-Nth-run branch, so a test written for the
ordinary path silently drove the rare one and passed on a fixture that returned
the same value either way. Set the selector explicitly, and confirm from the
code's own output — a log line, a spy, a counter — that the branch you meant is
the branch that ran. A seam that can only reset to the default cannot express
this: make it take the value.

A test that fails only because the symbol is missing has not been checked yet.
Against a base without the change, `is not a function` and a real assertion
failure are the same red, and only one of them means the test constrains
anything. Get the new code in place, then break its *behaviour* — pin the
return, neuter the branch, leave the signature alone — and watch the assertion
fail on the value. This is the case `check-new-tests` says it cannot see.

`bin/local/check-new-tests` does this from the diff — it runs the tests a
change adds against a worktree at the merge base and reports the ones that pass
there. Treat its two findings differently, because they are not equally strong.
A test that merely passes at base is usually fine: a negative or back-compat
case passes without the change by design, and on this repo's own history that
signal alone is right about one time in eight. A test that passes at base *and*
asserts the absence of something its own fixture never creates is the shape
that cannot fail, and that pair is what the check fails on. Neither verdict
replaces the revert above for a test you have reason to doubt — a test can
fail at base for a reason other than the one it was written for, and no runner
can see the difference.

A test that legitimately passes at base takes a marker, in the grammar
`ceiling:` uses, immediately above the test:

```bash
# passes-at-base: asserts behaviour this change was careful not to break
@test "a second call replaces the map rather than merging into it" {
```

A marker with no reason after the colon declares nothing and does not suppress
the finding, for the same reason a bare `ceiling:` does not satisfy its gate.

- A hand-run drill is a test, and fails the same way. An OOM drill whose command
  streamed its data (`head -c 400m /dev/zero | tail -c 1`) never held the memory
  it was meant to exhaust, exited 0 and killed nothing. Check the counter the drill
  exists to move (`oom_kill`, a request count) before and after, and treat an
  unmoved counter as the drill not having run
