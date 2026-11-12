"""Hedge a claimed fix that changed behaviour and shipped no test.

The fix pass asks its agent for a test beside every behavioural fix, and the
agent's `fixed` box is the only record of whether it wrote one. A box that names
a test, or says `No test: <why>`, is a claim; this module checks the one thing
that can be checked mechanically — whether the commit holds any file that is
the anchor's test — and reports the fix unverified when it holds none.

It does not judge whether a test is any good (the verify gate does) and it does
not demote the outcome: the fix still lands, and every surface that already
hedges an unverified fix carries the reason.
"""

# doc-group: pipeline

from __future__ import annotations

from collections.abc import Collection, Iterable
from pathlib import Path

import fix.scope
import fix.tracking
from core.trail import Trail, twarn
from pr.fix import ItemOutcome

# Anchors whose edits carry no behaviour a test could hold: prose, images,
# lockfiles and similar data. A denylist rather than an allowlist of code
# extensions, so a language this repo has never seen is treated as code.
_NON_BEHAVIOUR_SUFFIXES = frozenset({
    ".md", ".mdx", ".rst", ".txt", ".adoc",
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".webp", ".bmp",
    ".lock", ".sum", ".csv", ".pdf",
})
_NON_BEHAVIOUR_NAMES = frozenset({
    "package-lock.json", "yarn.lock", "pnpm-lock.yaml", "poetry.lock",
    "uv.lock", "go.sum", "cargo.lock", "gemfile.lock", "license", "changelog",
})


# What `verify_detail` opens with on a fix this module hedged; callers that
# count such fixes match on it rather than restating the words.
UNTESTED_DETAIL_PREFIX = "no test for"

def is_behaviour_bearing(path: str) -> bool:
    """Whether editing *path* can change what the program does."""
    p = Path(path)
    return (p.suffix.lower() not in _NON_BEHAVIOUR_SUFFIXES
            and p.name.lower() not in _NON_BEHAVIOUR_NAMES)


def _has_test(anchor: str, committed: Iterable[str]) -> bool:
    # `anchor_or_test` admits the anchor itself, which is always in the commit
    # and is the change, not the test for it.
    return any(
        path != anchor and fix.scope.anchor_or_test(path, {anchor})
        for path in committed
    )


def flag_untested(
    outcomes: list[ItemOutcome],
    committed: Collection[str] | None,
    anchors: dict[str, str],
    *,
    exempt_ids: frozenset[str] = frozenset(),
    trail: Trail | None = None,
) -> int:
    """Mark claimed fixes with no test in *committed* unverified, in place.

    Returns how many were flagged. *anchors* maps an outcome id to the file
    its fix was claimed in. An item is skipped when its anchor is prose or
    data, is itself a test or support file, is in *exempt_ids* (a machine-
    checked item has a checker as its oracle), or when its reason opens with
    `No test:` and gives a why. A prior gate verdict is overwritten to False
    and its detail kept after the regression reason.

    None *committed* means the pass could not attribute its edits, so there is
    no commit to hold a test and nothing is said.

    ceiling: attribution is by path — a test file in the commit named for the
    anchor counts as that item's test even when it was written for another.
    Upgrade trigger: when passes show a test credited to the wrong item.
    """
    if committed is None:
        return 0
    flagged = 0
    for outcome in outcomes:
        anchor = anchors.get(outcome.id, "")
        if not outcome.outcome.counts_as_fixed or not anchor:
            continue
        if outcome.id in exempt_ids or not is_behaviour_bearing(anchor):
            continue
        if _is_test_file(anchor):
            continue
        if _has_test(anchor, committed):
            continue
        evidence = fix.tracking.classify_test_evidence(outcome.reason)
        if evidence is fix.tracking.RegressionEvidence.EXEMPT:
            continue
        detail = f"{UNTESTED_DETAIL_PREFIX} {anchor} in this commit"
        if outcome.verify_detail:
            detail += f" (gate: {outcome.verify_detail})"
        outcome.verified = False
        outcome.verify_detail = detail
        flagged += 1
        twarn(trail, "fix_untested", detail,
              data={"item": outcome.id, "anchor": anchor,
                    "evidence": evidence.value})
    return flagged


def _is_test_file(path: str) -> bool:
    """Whether *path* is itself a test or support module.

    A fix whose anchor is a test file has no further test to ask for.
    """
    p = Path(path)
    return (fix.scope._in_test_dir(p)
            or bool(fix.scope._candidate_subject(p))
            or p.name == "conftest.py")


def check(
    adapter, outcomes: list[ItemOutcome], committed: Collection[str] | None,
    anchors: dict[str, str], trail: Trail | None = None,
) -> int:
    """`flag_untested` for a pass whose *adapter* asks for tests; 0 for one that does not.

    Keeps the engine's one call free of the adapter's two attributes.
    """
    if not adapter.requires_tests:
        return 0
    return flag_untested(
        outcomes, committed, anchors,
        exempt_ids=adapter.test_exempt_ids(), trail=trail,
    )
