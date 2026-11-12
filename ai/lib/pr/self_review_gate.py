"""The verdict type and callable contract for whether a self-review covers HEAD.

`SelfReviewVerdict` is what a gate returns; `SelfReviewGate` is a callable
from a HEAD sha to that verdict. `pr create` takes the callable so this
layer never reads the review pipeline.
"""

# doc-group: publishing

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class SelfReviewVerdict:
    """Whether a self-review covers a commit, and why."""

    covered: bool
    reason: str
    review_file: str = ""
    reviewed_through: str = ""


SelfReviewGate = Callable[[str], SelfReviewVerdict]
