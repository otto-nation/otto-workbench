"""Tests for fix.engine running the repo's own checks between the agent and
the commit, and the landing seeing their result.
"""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.invoke  # noqa: E402
import fix.engine  # noqa: E402
from fix.types import FixItem  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402
import fix.suite  # noqa: E402
import core.publishing  # noqa: E402
import rebase.prepush  # noqa: E402
from config.workbench_config import FixConfig, WorkbenchConfig  # noqa: E402

from fix_engine_support import StubAdapter, _answer, landed, head, _reads
# autouse: stubs the worktree snapshots every pass reads; imported so it applies here
from fix_engine_support import snapshots  # noqa: F401


def _git_worktree(tmp_path):
    """A committed repo whose one file the test then edits.

    `fix.blame` reads `git diff HEAD`, so a bare `tmp_path` has no change to
    describe and the pointer would be empty for a reason unrelated to what is
    under test.
    """
    import subprocess
    wt = tmp_path / "wt"
    wt.mkdir()
    (wt / "a.py").write_text("LOST_SENTINEL = 1\n")
    for args in (("init", "--quiet"), ("config", "user.email", "t@e.com"),
                 ("config", "user.name", "T"), ("add", "-A"),
                 ("commit", "-qm", "base")):
        subprocess.run(["git", "-C", str(wt), *args], check=True,
                       capture_output=True)
    return wt

# ── the repo's own checks, between the agent and the commit ─────────────────


class TestVerifySuite:
    """That the pass runs the repo's checks, and that `landing` sees the result.

    The ordering is the point. Two agents already check the pass's claims; this
    is the only thing that asks whether the pass broke something no claim
    mentions, and it has to answer before the commit body is rendered — a body
    reading `4 fixed, 0 skipped` over a red suite is the defect, not a
    cosmetic complaint about it.
    """

    def _configured(self, tmp_path, command):
        adapter = StubAdapter(tmp_path)
        adapter.config = WorkbenchConfig(
            fix=FixConfig(verify_command=command, verify_timeout=30))
        return adapter

    def _script(self, tmp_path, body):
        path = tmp_path / "checks"
        path.write_text(f"#!/usr/bin/env bash\n{body}\n")
        path.chmod(0o755)
        return str(path)

    def test_the_declared_command_runs_when_the_pass_claimed_a_fix(
        self, tmp_path, landed, head, snapshots,
    ):
        marker = tmp_path / "ran"
        adapter = self._configured(
            tmp_path, self._script(tmp_path, f"touch {marker}; exit 0"))
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert marker.exists()

    def test_landing_is_handed_the_verdict_before_it_writes_the_body(
        self, tmp_path, landed, head, snapshots,
    ):
        """The adapter reads `self.suite` while assembling its commit message."""
        adapter = self._configured(
            tmp_path, self._script(tmp_path, "echo 'E  boom'; exit 1"))
        snapshots.side_effect = _reads(set(), {"a.py"})
        seen = {}
        original = adapter.landing

        def capture(outcomes, changed):
            seen["status"] = adapter.suite.status
            seen["verified"] = [o.verified for o in outcomes]
            return original(outcomes, changed)

        adapter.landing = capture
        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert seen["status"] is fix.suite.SuiteStatus.RED
        assert seen["verified"] == [False]

    def test_a_red_run_withdraws_the_claim_before_the_commit(
        self, tmp_path, landed, head, snapshots,
    ):
        adapter = self._configured(
            tmp_path, self._script(tmp_path, "exit 1"))
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            run = fix.engine.run(adapter)

        assert run.suite.status is fix.suite.SuiteStatus.RED
        assert run.outcomes[0].outcome is FixOutcome.FIXED
        assert run.outcomes[0].verified is False

    def test_a_red_run_does_not_stop_the_work_from_landing(
        self, tmp_path, landed, head, snapshots,
    ):
        """The edits are real whatever the checks said; losing them is worse.

        The pass holds its push, so a red result still precedes anything
        leaving the machine — what it buys is an honest body, not a block.
        """
        adapter = self._configured(tmp_path, self._script(tmp_path, "exit 1"))
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert landed.called

    def test_after_verify_sees_the_suite_verdict_not_a_stale_one(
        self, tmp_path, landed, head, snapshots,
    ):
        """A red suite has to reach the hook that decides whether to publish.

        `after_verify` is where a domain stops the round speaking outward, and
        `pr.triage_round.hold_after_verify` describes the case it exists for
        as "something ran and the fix did not hold" — which is a red suite in
        as many words. Running the suite after this hook let the comments pass
        reply `Fixed in <sha>` to a reviewer over a tree whose checks were
        failing, because the verdict landed after the only thing that could
        have held it.
        """
        adapter = self._configured(tmp_path, self._script(tmp_path, "exit 1"))
        snapshots.side_effect = _reads(set(), {"a.py"})
        seen = {}
        adapter.after_verify = lambda outcomes: seen.update(
            verified=[o.verified for o in outcomes],
            suite=adapter.suite.status,
        )

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert seen["suite"] is fix.suite.SuiteStatus.RED
        assert seen["verified"] == [False], (
            "after_verify saw the per-item gate's verdict but not the suite's"
        )

    def test_a_red_suite_holds_publishing_for_every_domain(
        self, tmp_path, landed, head, snapshots, publishing_on,
    ):
        """Nothing replies, resolves or pushes off a tree whose checks fail.

        The per-item hooks cannot reach this case. `hold_after_verify`
        selects on `outcome.outcome in NEEDS_A_PERSON` before it reads
        `.verified`, and a suite demotion deliberately leaves the item at
        FIXED — so a suite-only failure is invisible to every per-item
        filter. Reordering the suite ahead of `after_verify` was necessary
        and not sufficient; this is the part that actually shuts the gate.
        """
        adapter = self._configured(tmp_path, self._script(tmp_path, "exit 1"))
        snapshots.side_effect = _reads(set(), {"a.py"})
        assert core.publishing.enabled()

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert not core.publishing.enabled()
        assert "checks are red" in core.publishing.held()

    def test_a_red_run_names_the_item_whose_file_lost_the_symbol(
        self, tmp_path, landed, head, snapshots, publishing_on,
    ):
        """End to end: the pointer reaches the commit body the engine lands.

        The engine is the only layer holding both halves — the pass's items
        with their anchors, and the failure text — so this is the wiring no
        unit test of `fix.blame` can cover.
        """
        wt = _git_worktree(tmp_path)
        (wt / "a.py").write_text("")       # the agent's edit: the symbol is gone
        failing = self._script(
            tmp_path, "echo \"AttributeError: no attribute 'LOST_SENTINEL'\"; exit 1")
        adapter = self._configured(wt, failing)
        adapter.items = lambda: [FixItem(id="i0", file="a.py", line=1,
                                         label="x", body="b")]
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            run = fix.engine.run(adapter)

        assert [p.item_id for p in run.suite.pointers] == ["i0"]
        assert run.suite.pointers[0].symbols == ("LOST_SENTINEL",)

    def test_a_deferred_item_is_never_pointed_at(
        self, tmp_path, landed, head, snapshots, publishing_on,
    ):
        """It claimed nothing, so a red suite has nothing of its to contradict.

        Pointing at it would send a reader to the one place the pass says it
        did not touch.
        """
        wt = _git_worktree(tmp_path)
        (wt / "a.py").write_text("")
        failing = self._script(
            tmp_path, "echo \"AttributeError: no attribute 'LOST_SENTINEL'\"; exit 1")
        adapter = self._configured(wt, failing)
        adapter.items = lambda: [FixItem(id="i0", file="a.py", line=1,
                                         label="x", body="b")]
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix",
                          _answer(adapter, tick="declined", reason="no")):
            run = fix.engine.run(adapter)

        assert run.suite.pointers == ()

    def test_a_green_run_over_files_the_commit_leaves_out_is_partial(
        self, tmp_path, landed, head, snapshots,
    ):
        """The checks run in the worktree, where a dropped file still sits.

        A pass committed a suite whose helper module it left out of the commit;
        the checks imported the module off disk, and the commit read as
        checked. The verdict has to say it was not over the commit.
        """
        adapter = self._configured(tmp_path, self._script(tmp_path, "exit 0"))
        adapter.commit_scope = lambda changed: {"a.py"}
        snapshots.side_effect = _reads(set(), {"a.py", "tests/a_support.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert adapter.suite.status is fix.suite.SuiteStatus.PARTIAL
        assert adapter.suite.left_out == ("tests/a_support.py",)
        assert adapter.landing_scope == {"a.py"}
        assert "Fix-Checks: partial" in landed.call_args.kwargs["message"]

    def test_a_green_run_over_exactly_the_commit_stays_green(
        self, tmp_path, landed, head, snapshots,
    ):
        adapter = self._configured(tmp_path, self._script(tmp_path, "exit 0"))
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert adapter.suite.status is fix.suite.SuiteStatus.GREEN

    def test_a_green_suite_leaves_publishing_open(
        self, tmp_path, landed, head, snapshots, publishing_on,
    ):
        """The hold is monotonic, so opening it wrongly cannot be undone."""
        adapter = self._configured(tmp_path, self._script(tmp_path, "exit 0"))
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert core.publishing.enabled()

    def test_checks_that_did_not_answer_do_not_hold_publishing(
        self, tmp_path, landed, head, snapshots, publishing_on,
    ):
        """A timeout is not evidence about the code, so it is not a reason to hold."""
        adapter = self._configured(tmp_path, str(tmp_path / "nope"))
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert core.publishing.enabled()

    def test_a_pass_that_claimed_nothing_does_not_pay_for_the_checks(
        self, tmp_path, landed, head, snapshots,
    ):
        """Minutes of test runtime to restate what the last run already said."""
        marker = tmp_path / "ran"
        adapter = self._configured(
            tmp_path, self._script(tmp_path, f"touch {marker}; exit 0"))
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix",
                          _answer(adapter, tick="declined", reason="wrong")):
            run = fix.engine.run(adapter)

        assert not marker.exists()
        assert run.suite.status is fix.suite.SuiteStatus.NOT_ATTEMPTED

    def test_a_pass_that_wrote_no_files_does_not_pay_for_the_checks(
        self, tmp_path, landed, head, snapshots,
    ):
        marker = tmp_path / "ran"
        adapter = self._configured(
            tmp_path, self._script(tmp_path, f"touch {marker}; exit 0"))
        snapshots.side_effect = _reads(set(), set())

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter)

        assert not marker.exists()

    def test_a_repo_declaring_no_command_says_so_rather_than_passing(
        self, tmp_path, landed, head, snapshots,
    ):
        """The state that let a red suite ship under a clean-looking summary."""
        adapter = StubAdapter(tmp_path)
        adapter.config = WorkbenchConfig()
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            run = fix.engine.run(adapter)

        assert run.suite.status is fix.suite.SuiteStatus.NOT_DECLARED
        assert run.outcomes[0].verified is None

    def test_a_domain_can_opt_out_where_the_checks_run_right_after_it(
        self, tmp_path, landed, head, snapshots,
    ):
        """`rebase.prepush` is the one, and the opt-out has to actually skip.

        It runs because the repo's checks just failed and it pushes the moment
        it lands, which runs them again for real. A third run in between would
        triple the slowest part of a rebase for a verdict arriving seconds
        later.
        """
        marker = tmp_path / "ran"
        adapter = self._configured(
            tmp_path, self._script(tmp_path, f"touch {marker}; exit 0"))
        adapter.verifies_with_suite = False
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            run = fix.engine.run(adapter)

        assert not marker.exists()
        assert run.suite.status is fix.suite.SuiteStatus.NOT_ATTEMPTED

    def test_every_other_domain_is_opted_in_without_saying_so(self):
        """The default is on: a domain gains the checks by declaring nothing.

        The hole this closes is a pass whose push is held, which is the shape
        of every domain but one. Defaulting off would reopen it for whichever
        adapter is written next.
        """
        assert fix.engine.FixAdapter.verifies_with_suite is True
        assert rebase.prepush.PrePushFixAdapter.verifies_with_suite is False

    def test_a_broken_declaration_does_not_take_the_pass_down_with_it(
        self, tmp_path, landed, head, snapshots,
    ):
        adapter = self._configured(tmp_path, str(tmp_path / "does-not-exist"))
        snapshots.side_effect = _reads(set(), {"a.py"})

        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            run = fix.engine.run(adapter)

        assert run.suite.status is fix.suite.SuiteStatus.ERROR
        assert landed.called
