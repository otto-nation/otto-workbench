"""Tests for fix.suite — the repo's own checks, run over a fix pass's work.

The two agents in the pipeline check the pass's *claims*. This module is the
only thing that asks whether the pass broke something no claim mentions, so
what is held here is that question's edges: which results withdraw a claim,
which merely report, and which say nothing at all.

The command is a real subprocess against a real script in `tmp_path` rather
than a patched `subprocess.run`. The contract under test is "exit 0 is the only
green", and a mock returning a returncode is the test agreeing with itself
about what a process is.
"""

import os
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from fix import suite as fix_suite  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402


class _RecordingTrail:
    """A trail that keeps what it was told, so a test can read it back."""

    def __init__(self):
        self.events = []

    def info(self, action, detail, data=None):
        self.events.append(("info", action, detail, data or {}))

    def warn(self, action, detail, data=None):
        self.events.append(("warn", action, detail, data or {}))

    def error(self, action, detail, data=None):
        self.events.append(("error", action, detail, data or {}))


def _script(tmp_path: Path, name: str, body: str) -> str:
    """A real executable in `tmp_path`, and the argv string that runs it."""
    path = tmp_path / name
    path.write_text(f"#!/usr/bin/env bash\n{body}\n")
    path.chmod(0o755)
    return str(path)


def _fixed(item_id: str = "N1", **kwargs) -> ItemOutcome:
    return ItemOutcome(id=item_id, outcome=FixOutcome.FIXED, **kwargs)


# ── what the command's exit status means ────────────────────────────────────


def test_exit_zero_is_the_only_green(tmp_path):
    cmd = _script(tmp_path, "green", "exit 0")

    assert fix_suite.run(tmp_path, cmd, 30).status is fix_suite.SuiteStatus.GREEN


def test_a_non_zero_exit_is_red_even_when_it_printed_nothing_alarming(tmp_path):
    """A runner that says "ok" and exits 1 is red.

    The regression this module exists for shipped under a summary that read
    well. Output is evidence for a person; the status is the verdict.
    """
    cmd = _script(tmp_path, "red", "echo 'all good'; exit 1")

    result = fix_suite.run(tmp_path, cmd, 30)

    assert result.status is fix_suite.SuiteStatus.RED
    assert result.demotes is True


def test_a_red_run_carries_its_output_so_the_commit_body_can_show_it(tmp_path):
    cmd = _script(tmp_path, "red", "echo 'E   AttributeError: no such attr'; exit 1")

    assert "AttributeError" in fix_suite.run(tmp_path, cmd, 30).output_tail


def test_a_green_run_carries_no_output(tmp_path):
    """Green output is noise in a commit body, and there is a lot of it."""
    cmd = _script(tmp_path, "green", "echo '500 passed'; exit 0")

    assert fix_suite.run(tmp_path, cmd, 30).output_tail == ""


def test_stderr_reaches_the_tail_as_well_as_stdout(tmp_path):
    """Both bats and pytest put some of what failed on stderr."""
    cmd = _script(tmp_path, "red", "echo 'boom' >&2; exit 2")

    assert "boom" in fix_suite.run(tmp_path, cmd, 30).output_tail


def test_stdout_and_stderr_are_labeled_when_both_have_something_to_say(tmp_path):
    """An interleaved failure reads as two labeled blocks, not one run-on.

    `stdout` and `stderr` land back-to-back with nothing saying which stream
    produced which text otherwise — a traceback on stderr next to output
    already flushed to stdout, indistinguishable in the clipped tail.
    """
    cmd = _script(tmp_path, "red", "echo 'out line'; echo 'err line' >&2; exit 1")

    tail = fix_suite.run(tmp_path, cmd, 30).output_tail

    assert tail.index("out line") < tail.index("--- stderr ---")
    assert tail.index("--- stderr ---") < tail.index("err line")


def test_the_command_runs_in_the_worktree_it_was_given(tmp_path):
    """The tree under test is the one the agent edited, not the caller's cwd."""
    marker = tmp_path / "only-here"
    marker.write_text("x")
    cmd = _script(tmp_path, "check", "test -f only-here")

    assert fix_suite.run(tmp_path, cmd, 30).status is fix_suite.SuiteStatus.GREEN


def test_the_tail_is_clipped_from_the_end_not_the_start(tmp_path):
    """Runners put the summary of what failed last; a head clip is the banner."""
    body = "for i in $(seq 1 4000); do echo \"line $i\"; done; exit 1"
    cmd = _script(tmp_path, "verbose", body)

    tail = fix_suite.run(tmp_path, cmd, 30).output_tail

    assert "line 4000" in tail
    assert "line 1\n" not in tail
    assert tail.startswith("[...]")


# ── the states that are not a verdict about the code ────────────────────────


def test_no_declared_command_is_reported_rather_than_passed_over(tmp_path):
    """The state the whole module exists to make visible.

    A repo that declares nothing and a repo whose checks came back clean
    rendered identically before this, which is how `4 fixed, 0 skipped` got
    written over a suite that never ran.
    """
    result = fix_suite.run(tmp_path, "", 30)

    assert result.status is fix_suite.SuiteStatus.NOT_DECLARED
    assert result.reportable is True
    assert "fix.verify_command" in result.note


def test_not_declared_reaches_the_trail_like_every_other_reportable_status(tmp_path):
    """NOT_DECLARED is the one reportable status `run` used to return early on,
    before ever calling `_report` — so it never reached `otto-log`, unlike
    GREEN, RED, TIMED_OUT and ERROR, which all go through it. An operator
    auditing trail history for "did this repo ever run its checks" got no
    signal for the not-declared case.
    """
    trail = _RecordingTrail()

    fix_suite.run(tmp_path, "", 30, trail)

    assert len(trail.events) == 1
    kind, action, detail, _data = trail.events[0]
    assert kind == "warn"
    assert action == "fix_verify_suite"
    assert "fix.verify_command" in detail


def test_a_declaration_that_parses_to_nothing_is_an_error_not_an_absence(tmp_path):
    """Whitespace is absence; a command naming no real program is broken.

    `shlex.split` does not treat `#` as a comment marker by default, so
    `"# nothing here"` does not parse to an empty argv — it tokenizes to
    `['#', 'nothing', 'here']`, and fails because no program named `#` can be
    started. That is the `except OSError` branch in `fix.suite.run`, not the
    `if not argv:` branch a `#`-only declaration might suggest; both land on
    `SuiteStatus.ERROR`, but for different reasons. The `if not argv:` branch
    is not reachable through `run`'s public surface: any string that is not
    all whitespace already produces at least one shlex token, and an
    all-whitespace string is caught earlier as `NOT_DECLARED`.
    """
    result = fix_suite.run(tmp_path, "   ", 30)
    assert result.status is fix_suite.SuiteStatus.NOT_DECLARED

    broken = fix_suite.run(tmp_path, "# nothing here", 30)
    assert broken.status is fix_suite.SuiteStatus.ERROR
    assert broken.command == "# nothing here"


def test_a_non_positive_timeout_is_refused_rather_than_reported_as_timed_out(tmp_path):
    """`Popen.communicate(timeout=0)` treats a non-positive budget as already
    elapsed, so an unclamped `fix.verify_timeout` of 0 (or less) would report a
    fast, correctly-declared suite as `TIMED_OUT` — misreporting a bad config
    value as a slow suite. It is refused before the process is even started.
    """
    cmd = _script(tmp_path, "green", "exit 0")

    zero = fix_suite.run(tmp_path, cmd, 0)
    assert zero.status is fix_suite.SuiteStatus.ERROR
    assert "fix.verify_timeout" in zero.output_tail

    negative = fix_suite.run(tmp_path, cmd, -5)
    assert negative.status is fix_suite.SuiteStatus.ERROR
    assert "fix.verify_timeout" in negative.output_tail


def test_an_unparseable_declaration_is_an_error_and_not_a_crash(tmp_path):
    result = fix_suite.run(tmp_path, 'run --flag "unclosed', 30)

    assert result.status is fix_suite.SuiteStatus.ERROR
    assert result.demotes is False


def test_a_command_that_does_not_exist_is_an_error_not_a_red_suite(tmp_path):
    """A broken declaration is not a finding about the branch.

    Reporting it as red would send a reader hunting a regression that is not
    there; reporting it as green would be the silence this replaces.
    """
    result = fix_suite.run(tmp_path, str(tmp_path / "nope"), 30)

    assert result.status is fix_suite.SuiteStatus.ERROR
    assert result.demotes is False
    assert result.ran is False


def test_a_timeout_establishes_nothing_either_way(tmp_path):
    cmd = _script(tmp_path, "slow", "sleep 5")

    result = fix_suite.run(tmp_path, cmd, 1)

    assert result.status is fix_suite.SuiteStatus.TIMED_OUT
    assert result.demotes is False
    assert result.ran is False
    assert "did not finish" in result.note


# How long the fixture's orphan would live if nothing killed it. Comfortably
# longer than _REAP_BUDGET_S, so "the run came back" cannot be the child
# having finished on its own.
_ORPHAN_LIFETIME_S = 40
# What the runner gets before the timeout fires. Not the tight bound it looks
# like: it has to cover bash starting, forking and writing a file on a machine
# running the whole suite across twelve workers. At 1s this test failed in CI
# for that reason alone — the timeout beat the fixture to its own pid file —
# which is a flake in the test and not a finding about the code.
_FIXTURE_TIMEOUT_S = 5
# The wall-clock ceiling the assertion reads. Between the timeout above and
# the lifetime above, so a run that signalled the group lands well under it
# and one that waited out the orphan lands well over.
_REAP_BUDGET_S = 20


def test_a_timeout_reaps_the_whole_process_tree(tmp_path):
    """A test runner is a tree, and `subprocess.run`'s timeout kills one process.

    This repo's runner forks up to twelve workers. Orphaning them leaves them
    competing for the worktree the pass is about to commit into, long after
    the pass reported and exited.

    Both halves are asserted, because either alone passes against the bug. A
    child-only kill leaves `communicate` holding the inherited pipe until the
    orphan exits by itself, so the tree *is* empty by the time the call
    returns — it just took the orphan's full lifetime to get there. The
    elapsed bound is what tells those apart, and it is the assertion that
    fails at ~40s against the old behaviour.
    """
    child_pid = tmp_path / "child.pid"
    cmd = _script(
        tmp_path, "forker",
        f"sleep {_ORPHAN_LIFETIME_S} &\necho $! > {child_pid}\nwait",
    )

    started = time.monotonic()
    result = fix_suite.run(tmp_path, cmd, _FIXTURE_TIMEOUT_S)
    elapsed = time.monotonic() - started

    assert result.status is fix_suite.SuiteStatus.TIMED_OUT
    assert elapsed < _REAP_BUDGET_S, (
        f"the run took {elapsed:.0f}s to come back from a "
        f"{_FIXTURE_TIMEOUT_S}s timeout — it waited out the orphan rather "
        "than signalling the group"
    )
    assert child_pid.exists(), (
        f"the fixture did not record its child within {_FIXTURE_TIMEOUT_S}s — "
        "the machine was too loaded for the fixture, not a finding about the "
        "code under test"
    )
    pid = int(child_pid.read_text().strip())
    assert not _alive(pid), f"pid {pid} outlived the run that spawned it"


def _alive(pid: int) -> bool:
    """Whether `pid` still exists. Signal 0 tests without delivering."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


# ── what a result does to the pass's claims ─────────────────────────────────


def test_a_red_run_withdraws_every_claimed_fix(tmp_path):
    outcomes = [_fixed("N1"), _fixed("N2")]
    red = fix_suite.SuiteResult(status=fix_suite.SuiteStatus.RED, command="c")

    assert fix_suite.apply_to(outcomes, red) == 2
    assert [o.verified for o in outcomes] == [False, False]
    assert all(fix_suite.RED_DETAIL in o.verify_detail for o in outcomes)


def test_a_red_run_does_not_demote_an_item_out_of_fixed(tmp_path):
    """One command over the whole pass cannot say which edit was wrong.

    The message reword that motivated this was a correct fix with a stale test
    beside it. Demoting it out of FIXED would record the right change as the
    defect.
    """
    outcomes = [_fixed("N1")]
    fix_suite.apply_to(
        outcomes, fix_suite.SuiteResult(status=fix_suite.SuiteStatus.RED))

    assert outcomes[0].outcome is FixOutcome.FIXED


def test_a_red_run_keeps_the_gate_s_own_detail_beside_its_own(tmp_path):
    """Two different claims about two different things; neither replaces the other."""
    outcomes = [_fixed("N1", verify_detail="test_foo covers this")]
    fix_suite.apply_to(
        outcomes, fix_suite.SuiteResult(status=fix_suite.SuiteStatus.RED))

    assert "test_foo covers this" in outcomes[0].verify_detail
    assert fix_suite.RED_DETAIL in outcomes[0].verify_detail


def test_a_green_run_promotes_nothing(tmp_path):
    """Green is about the tree; `verified` is about an item.

    Crediting each of sixteen items with one batch-level result is the
    path-attribution ceiling walked from the other end.
    """
    outcomes = [_fixed("N1")]
    green = fix_suite.SuiteResult(status=fix_suite.SuiteStatus.GREEN)

    assert fix_suite.apply_to(outcomes, green) == 0
    assert outcomes[0].verified is None


def test_a_timeout_leaves_the_claims_where_the_gate_put_them(tmp_path):
    outcomes = [_fixed("N1", verified=True)]
    timed_out = fix_suite.SuiteResult(status=fix_suite.SuiteStatus.TIMED_OUT)

    assert fix_suite.apply_to(outcomes, timed_out) == 0
    assert outcomes[0].verified is True


def test_only_claimed_fixes_are_withdrawn(tmp_path):
    """A deferral claimed nothing, so a red suite takes nothing from it."""
    deferred = ItemOutcome(id="N2", outcome=FixOutcome.DEFERRED)
    outcomes = [_fixed("N1"), deferred]

    fix_suite.apply_to(
        outcomes, fix_suite.SuiteResult(status=fix_suite.SuiteStatus.RED))

    assert deferred.verified is None


# ── when the checks are worth running at all ────────────────────────────────


@pytest.mark.parametrize(
    ("outcomes", "changed", "expected", "why"),
    [
        ([FixOutcome.FIXED], {"a.py"}, True, "a claimed fix with files behind it"),
        ([FixOutcome.FIXED], set(), False, "nothing was written to check"),
        ([FixOutcome.FIXED], None, False, "the worktree could not be read"),
        ([FixOutcome.DEFERRED], {"a.py"}, False, "no claim to withdraw"),
        ([FixOutcome.DECLINED], {"a.py"}, False, "no claim to withdraw"),
        ([FixOutcome.DEFERRED, FixOutcome.FIXED], {"a.py"}, True, "one claim is enough"),
    ],
)
def test_should_run_asks_whether_there_is_a_claim_and_a_tree(
    outcomes, changed, expected, why,
):
    items = [ItemOutcome(id=f"i{n}", outcome=o) for n, o in enumerate(outcomes)]

    assert fix_suite.should_run(items, changed) is expected, why


def test_a_pass_with_nothing_to_check_stays_silent(tmp_path):
    """Distinct from a repo that declared nothing, which must speak.

    A caveat printed on every pass that fixes nothing is a caveat nobody reads
    on the pass that earned one.
    """
    assert fix_suite.SuiteResult().status is fix_suite.SuiteStatus.NOT_ATTEMPTED
    assert fix_suite.SuiteResult().reportable is False
    assert fix_suite.SuiteResult().note == ""
