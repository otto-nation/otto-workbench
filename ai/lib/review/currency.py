"""Whether a stored self-review covers a commit.

`self_review_verdict` is the one owner of that answer: batch planning and
`pr create` both ask it, so they cannot disagree about currency.
"""

# doc-group: pipeline

from __future__ import annotations

from pathlib import Path

import git.client
import review.document
import review.store
from pr.self_review_gate import SelfReviewVerdict


def self_review_verdict(review_file: Path, subject: str) -> SelfReviewVerdict:
    """Whether the self-review at *review_file* covers commit *subject*."""
    stored = review.store.read_review(review_file)
    path = str(review_file)
    if stored is None:
        reason = (
            "self-review predates review.json"
            if review_file.is_file()
            else "no self-review yet"
        )
        return SelfReviewVerdict(False, reason, review_file=path)
    reviewed = stored.meta.reviewed_through
    if review.document.covers(reviewed, subject):
        return SelfReviewVerdict(
            True, "self-review is current",
            review_file=path, reviewed_through=reviewed,
        )
    of = f"self-review is of {git.client.abbrev(reviewed) or 'an unknown head'}"
    return SelfReviewVerdict(
        False, of, review_file=path, reviewed_through=reviewed,
    )
