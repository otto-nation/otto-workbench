"""Tests for fix.regression — a claimed fix with no test in its commit is hedged.

The verify gate judges whether a named test is any good. What is held here is
the mechanical half: given the files a commit holds, which claimed fixes owe a
test they did not bring, and what the hedge says.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import fix.regression  # noqa: E402
import fix.tracking  # noqa: E402
from fix.tracking import RegressionEvidence, classify_test_evidence  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402

from fix_suite_support import _RecordingTrail  # noqa: E402


def _fixed(item_id="M1", **kwargs) -> ItemOutcome:
    return ItemOutcome(id=item_id, outcome=FixOutcome.FIXED, **kwargs)


def _flag(outcomes, committed, anchors=None, **kwargs):
    anchors = anchors if anchors is not None else {o.id: "src/mod.py" for o in outcomes}
    return fix.regression.flag_untested(outcomes, committed, anchors, **kwargs)


class TestClassifyTestEvidence:
    def test_the_prefix_with_a_reason_is_an_exemption(self):
        assert classify_test_evidence("No test: prose change") is RegressionEvidence.EXEMPT

    def test_the_prefix_is_case_insensitive_and_stripped(self):
        assert classify_test_evidence("  no TEST:  rename only ") is RegressionEvidence.EXEMPT

    def test_a_bare_prefix_is_not_an_exemption(self):
        assert classify_test_evidence("No test:") is RegressionEvidence.NAMED
        assert classify_test_evidence("No test:   ") is RegressionEvidence.NAMED

    def test_an_empty_reason_is_missing(self):
        assert classify_test_evidence("") is RegressionEvidence.MISSING
        assert classify_test_evidence("  \n") is RegressionEvidence.MISSING

    def test_anything_else_names_a_test(self):
        assert classify_test_evidence("Test: test_mod — asserts x") is RegressionEvidence.NAMED

    def test_the_prefix_constant_is_the_documented_spelling(self):
        assert fix.tracking.NO_TEST_PREFIX == "No test:"


class TestFlagUntested:
    def test_a_behavioural_fix_with_no_test_is_reported_unverified(self):
        outcome = _fixed(reason="Test: test_mod — asserts x")
        trail = _RecordingTrail()

        assert _flag([outcome], {"src/mod.py"}, trail=trail) == 1

        assert outcome.verified is False
        assert outcome.verify_detail == "no test for src/mod.py in this commit"
        assert [e for e in trail.events if e[:2] == ("warn", "fix_untested")]

    def test_a_test_for_the_anchor_in_the_commit_satisfies_it(self):
        outcome = _fixed()
        assert _flag([outcome], {"src/mod.py", "tests/mod_test.py"}) == 0
        assert outcome.verified is None

    def test_a_test_for_another_file_does_not(self):
        outcome = _fixed()
        assert _flag([outcome], {"src/mod.py", "tests/other_test.py"}) == 1

    def test_no_test_with_a_reason_is_exempt(self):
        outcome = _fixed(reason="No test: log wording")
        assert _flag([outcome], {"src/mod.py"}) == 0
        assert outcome.verified is None

    def test_a_bare_no_test_is_not_exempt(self):
        assert _flag([_fixed(reason="No test:")], {"src/mod.py"}) == 1

    def test_a_docs_anchor_is_exempt(self):
        outcome = _fixed()
        assert _flag([outcome], {"docs/guide.md"}, {"M1": "docs/guide.md"}) == 0

    def test_a_static_analysis_id_is_exempt(self):
        outcome = _fixed("SA1")
        assert _flag([outcome], {"src/mod.py"}, exempt_ids=frozenset({"SA1"})) == 0

    def test_a_test_anchor_is_exempt(self):
        outcome = _fixed()
        assert _flag([outcome], {"tests/mod_test.py"}, {"M1": "tests/mod_test.py"}) == 0

    def test_an_unattributable_pass_says_nothing(self):
        outcome = _fixed()
        assert _flag([outcome], None) == 0
        assert outcome.verified is None

    def test_a_gate_pass_is_overwritten_to_unverified(self):
        outcome = _fixed(verified=True)
        _flag([outcome], {"src/mod.py"})
        assert outcome.verified is False

    def test_the_regression_reason_leads_and_the_gate_text_follows(self):
        outcome = _fixed(verified=False, verify_detail="suite red")
        _flag([outcome], {"src/mod.py"})
        assert outcome.verify_detail == (
            "no test for src/mod.py in this commit (gate: suite red)")

    def test_an_item_the_pass_did_not_fix_is_left_alone(self):
        outcome = ItemOutcome(id="M1", outcome=FixOutcome.DECLINED)
        assert _flag([outcome], {"src/mod.py"}) == 0
        assert outcome.verified is None
