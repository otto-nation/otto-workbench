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


def test_a_declaration_that_parses_to_nothing_is_an_error_not_an_absence(tmp_path):
    """Whitespace is absence; a `#` that shlex eats is a broken declaration."""
    result = fix_suite.run(tmp_path, "   ", 30)
    assert result.status is fix_suite.SuiteStatus.NOT_DECLARED

    broken = fix_suite.run(tmp_path, "# nothing here", 30)
    assert broken.status is fix_suite.SuiteStatus.ERROR
    assert broken.command == "# nothing here"


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


# Comfortably longer than the timeout the test gives the runner, so "the run
# came back" cannot be the child having finished on its own.
_ORPHAN_LIFETIME_S = 30
# What the run gets to notice the timeout, signal the group and reap it. Well
# under the lifetime above: the gap between them is what the assertion reads.
_REAP_BUDGET_S = 15


def test_a_timeout_reaps_the_whole_process_tree(tmp_path):
    """A test runner is a tree, and `subprocess.run`'s timeout kills one process.

    This repo's runner forks up to twelve workers. Orphaning them leaves them
    competing for the worktree the pass is about to commit into, long after
    the pass reported and exited.

    Both halves are asserted, because either alone passes against the bug. A
    child-only kill leaves `communicate` holding the inherited pipe until the
    orphan exits by itself, so the tree *is* empty by the time the call
    returns — it just took the orphan's full lifetime to get there. The
    elapsed bound is what tells those apart.
    """
    child_pid = tmp_path / "child.pid"
    cmd = _script(
        tmp_path, "forker",
        f"sleep {_ORPHAN_LIFETIME_S} & echo $! > {child_pid}\nwait",
    )

    started = time.monotonic()
    result = fix_suite.run(tmp_path, cmd, 1)
    elapsed = time.monotonic() - started

    assert result.status is fix_suite.SuiteStatus.TIMED_OUT
    assert elapsed < _REAP_BUDGET_S, (
        f"the run took {elapsed:.0f}s to come back from a 1s timeout \u2014 it "
        "waited out the orphan rather than signalling the group"
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
