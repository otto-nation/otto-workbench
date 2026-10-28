"""A finding with a path and no line belongs to the group holding its file."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from review.prompt_prior import _scoped  # noqa: E402
from review.reconcile import passed_over  # noqa: E402
from review.record import ReviewRecord  # noqa: E402
from review.steps import _carry_forward_prior_findings  # noqa: E402
from review.store import StoredReview  # noqa: E402
from review.types import Group, ReviewMeta  # noqa: E402
from review_convergence_support import finding  # noqa: E402

LINE_LESS = finding("I1", "module-level guard", path="a.py")


def _prior():
    return StoredReview(
        ReviewRecord(findings=(LINE_LESS, finding("S1", "x", path="b.py", line=3))),
        ReviewMeta(head_sha="p" * 40),
    )


def test_a_line_less_finding_is_scoped_to_the_group_holding_its_file():
    assert [f.id for f in _scoped(_prior().record, ["a.py"]).findings] == ["I1"]


def test_a_skipped_group_carries_its_line_less_findings():
    carried = _carry_forward_prior_findings(_prior(), [Group("g1", ["a.py"], 1)], {1})
    assert [f.id for f in carried.record.findings] == ["I1"]


def test_a_carried_line_less_finding_is_not_passed_over():
    prior = StoredReview(ReviewRecord(findings=(LINE_LESS,)), ReviewMeta(head_sha="p" * 40))
    carried = _carry_forward_prior_findings(prior, [Group("g1", ["a.py"], 1)], {1})
    assert passed_over(prior, carried.record) == []
