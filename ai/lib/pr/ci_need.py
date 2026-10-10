"""Whether a PR's check rollup needs CI attention, read from the rollup state alone.

`need` takes the head commit's `statusCheckRollup.state` (`SUCCESS`, `FAILURE`,
`ERROR`, `PENDING`, `EXPECTED`, or "" when GitHub reports no rollup) and says
whether a CI pass has work to do. Batch's plan asks it for every PR, and the
scheduler's `RUNNING` check uses it to decide when a CI step should wait
before fixing anything. Reading the rollup out of a GraphQL node stays with
the query that fetched it (`batch.plan.rollup_state`).

An empty rollup is "nothing needed", not green: a workflow waiting on
approval reports none. `ci-check` on such a commit finds no checks and exits
non-zero, which is a different answer to a different question.
`tests/pr_ci_need_test.py` pins the two side by side.
"""

# doc-group: pr-state

from __future__ import annotations

from dataclasses import dataclass

# Rollup states a CI step waits on rather than fixes straight away.
RUNNING = frozenset({"PENDING", "EXPECTED"})
FAILED = frozenset({"FAILURE", "ERROR"})


@dataclass(frozen=True)
class CINeed:
    needed: bool
    reason: str


def need(state: str) -> CINeed:
    """CI need for a head commit whose check rollup reports *state*."""
    if state in FAILED:
        return CINeed(True, f"checks {state.lower()}")
    if state in RUNNING:
        return CINeed(True, "checks running")
    if state == "SUCCESS":
        return CINeed(False, "checks green")
    # Not "green": a workflow waiting on approval reports no rollup at all.
    return CINeed(False, "no checks reported")
