"""Tests for the engine running the no-test check after both gates.

`fix_regression_test` holds what the check decides; here it is that the engine
calls it at the right moment and only for a domain that asked.
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
import fix.gate  # noqa: E402
import fix.verify  # noqa: E402

from fix_engine_support import StubAdapter, _answer, landed, head, _reads  # noqa: E402, F401
from fix_engine_support import snapshots  # noqa: E402, F401


def _run(tmp_path, snapshots, *, requires, exempt=frozenset(), changed=None,
         verdict=None):
    adapter = StubAdapter(tmp_path)
    adapter.requires_tests = requires
    adapter.test_exempt_ids = lambda: frozenset(exempt)
    snapshots.side_effect = _reads(set(), changed or {"a.py"})
    seen = {}
    original = adapter.after_verify

    def capture(outcomes):
        seen["verified"] = [(o.verified, o.verify_detail) for o in outcomes]
        original(outcomes)

    adapter.after_verify = capture
    verdicts = {"i0": verdict} if verdict else {}
    with patch.object(fix.verify, "run", side_effect=lambda *a, **k: dict(verdicts)):
        with patch.object(agent.invoke, "run_fix", _answer(adapter)):
            fix.engine.run(adapter, verify=fix.verify.run)
    return seen["verified"]


class TestRequiresTests:
    def test_a_domain_that_asks_has_an_untested_fix_flagged_before_after_verify(
        self, tmp_path, landed, head, snapshots,
    ):
        seen = _run(tmp_path, snapshots, requires=True)
        assert seen == [(False, "no test for a.py in this commit")]

    def test_the_check_runs_after_the_gate_so_its_text_follows(
        self, tmp_path, landed, head, snapshots,
    ):
        verdict = fix.gate.Verdict(ok=True, detail="looks right")
        seen = _run(tmp_path, snapshots, requires=True, verdict=verdict)
        assert seen == [
            (False, "no test for a.py in this commit (gate: looks right)")]

    def test_a_test_in_the_commit_leaves_the_verdict_alone(
        self, tmp_path, landed, head, snapshots,
    ):
        seen = _run(tmp_path, snapshots, requires=True,
                    changed={"a.py", "tests/a_test.py"})
        assert not seen[0][1].startswith("no test for")

    def test_an_exempt_id_is_not_flagged(
        self, tmp_path, landed, head, snapshots,
    ):
        seen = _run(tmp_path, snapshots, requires=True, exempt={"i0"})
        assert not seen[0][1].startswith("no test for")

    def test_a_domain_that_does_not_ask_is_unaffected(
        self, tmp_path, landed, head, snapshots,
    ):
        seen = _run(tmp_path, snapshots, requires=False)
        assert not seen[0][1].startswith("no test for")

    def test_the_default_is_off_with_no_exemptions(self):
        assert fix.engine.FixAdapter.requires_tests is False
        assert StubAdapter("/x").test_exempt_ids() == frozenset()
