"""The verify gate: when it runs, and what its verdicts mean.

``fix.verify`` owns the one call that produces verdicts. This module owns
everything around it — what the gate is shown for a fix, a decline and a
contradicted deferral, and how an answer it gives (or withholds) changes an
item's outcome. Split from ``fix.engine``, which owns the batch/invoke/retry
pipeline and nothing about judgement.
"""

# doc-group: pipeline

from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class Verdict:
    """What the gate established about one fix.

    ``ok`` is three-valued on purpose. True is "something ran against the
    changed path and passed", False is "something ran and it failed", and None
    is "nothing could be run". Collapsing the last two would demote a fix on a
    project with no runnable check, which is the whole class of work the gate is
    least able to judge and has the least right to overrule.

    ``detail`` is what ran and what came of it, in the words a reply prints. It
    matters most when ``ok`` is None: an unverified row is only actionable if it
    says why nobody could check it.
    """

    ok: bool | None
    detail: str = ""


# What the gate is handed and what it gives back: the fixed items, and a verdict
# per item id. An id the gate does not answer is not a verdict — see `_verify`.
VerifyFn = Callable[..., dict[str, Verdict]]
