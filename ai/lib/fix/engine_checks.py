"""The engine's half of ``fix.suite``: resolve the command, run it, apply.

A red result withdraws the pass's verification claims before ``landing``
renders a body that would otherwise say those claims held. Config is re-read
from the worktree rather than taken from ``adapter.config`` because a pass
launched from another directory carries the config of wherever it was
launched, and the checks belong to the tree being edited. An adapter that was
given one still wins: it is the same file when the two agree, and the
caller's explicit choice when they do not.
"""

# doc-group: pipeline

from __future__ import annotations

import dataclasses
import math
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import core.publishing
import fix.baseline
import fix.blame
import fix.suite
from config.workbench_config import load_config_or_default
from core.trail import tinfo
from fix.types import FixItem
from pr.fix import ItemOutcome

if TYPE_CHECKING:
    from config.workbench_config import WorkbenchConfig
    from core.trail import Trail
    from fix.engine import FixAdapter


def _verify_suite(
    adapter: FixAdapter, outcomes: list[ItemOutcome],
    changed: set[str] | None, trail: Trail | None,
    by_id: dict[str, FixItem] | None = None,
    *, base: str = "", left_out: set[str] | frozenset[str] = frozenset(),
    baseline_ref: str = "", dirty_before: frozenset[str] = frozenset(),
) -> fix.suite.SuiteResult:
    """Run the repo's declared checks over the pass's work and apply the verdict.

    The engine's half of `fix.suite`: resolve the command from the worktree's
    config, run it, and let a red result withdraw the pass's verification
    claims before `landing` renders a body that would otherwise say those
    claims held.

    *base* is the HEAD the pass started from, exported as `fix.suite.FIX_BASE_ENV`.

    *left_out* is what `commit_scope` keeps out of the commit. Those files are
    in the tree the checks run over, so a green run becomes PARTIAL — see
    `fix.suite.scoped_to`.

    Config is re-read from the worktree rather than taken from `adapter.config`
    because a pass launched from another directory carries the config of
    wherever it was launched, and the checks belong to the tree being edited.
    An adapter that was given one still wins: it is the same file when the two
    agree, and the caller's explicit choice when they do not.
    """
    if not adapter.verifies_with_suite or not fix.suite.should_run(outcomes, changed):
        return fix.suite.SuiteResult()
    config = adapter.config or load_config_or_default(adapter.workdir)
    result = _run_checks(
        adapter, config, trail, base=base, baseline_ref=baseline_ref,
        paths=(changed or set()) | dirty_before,
    )
    result = fix.suite.scoped_to(result, set(left_out))
    fix.suite.apply_to(outcomes, result)
    if result.demotes:
        # Only on red, and only as a lead. `fix.blame` reads the pass's own
        # diff against the failure text; it changes no outcome, because one
        # run over sixteen items still cannot say which broke the tree. What
        # it can say is which item's file lost the symbol the failure names,
        # which turns seven hedged rows into one worth reading first.
        result = dataclasses.replace(result, pointers=fix.blame.pointers(
            adapter.workdir, _anchors(outcomes, by_id or {}), result.output_tail,
        ))
        for pointer in result.pointers:
            tinfo(trail, "fix_verify_pointer", pointer.describe(),
                  data={"item": pointer.item_id, "file": pointer.file,
                        "symbols": list(pointer.symbols)})
    _hold_for_suite(result)
    return result


def _hold_for_suite(result: fix.suite.SuiteResult) -> None:
    """Re-place the publishing holds a suite result implies.

    Holds are process-local, so a resumed pass that skips the suite still
    owes the hold the original run would have placed. Extracted from
    `_verify_suite` so both paths share one owner.
    """
    if result.status is fix.suite.SuiteStatus.PREEXISTING:
        # Not demoting — the pass did not make the tree red — but held all the
        # same: nothing should push or reply off a red tree, whoever made it so.
        core.publishing.hold("the repo's checks were already red before this pass")
    if result.demotes:
        # Held here rather than left to a domain's `after_verify`, because a
        # red suite is a fact about the pass and not about any item in it.
        # `hold_after_verify` cannot reach it by construction: it selects on
        # `outcome.outcome in NEEDS_A_PERSON` before it reads `.verified`,
        # and `apply_to` deliberately leaves a demoted item at FIXED — so a
        # suite-only demotion is invisible to every per-item filter, in this
        # domain and any future one. Holding centrally is also wider than the
        # one domain that overrides the hook: nothing should reply, resolve,
        # or push off a tree whose own checks are failing.
        core.publishing.hold("the repo's checks are red with this pass's changes")


def _run_checks(
    adapter: FixAdapter, config: WorkbenchConfig, trail: Trail | None, *,
    base: str, baseline_ref: str, paths: set[str] | frozenset[str],
) -> fix.suite.SuiteResult:
    """The declared command over the pass's tree, and a red verdict re-asked at its base.

    *paths* is everything the tree differs from HEAD in — the agent's edits and
    whatever was dirty before them — written to a file both runs are handed, so
    the baseline selects the tests the pass run selected.
    """
    with tempfile.TemporaryDirectory(prefix="fix-paths-") as scratch:
        listed = Path(scratch) / "paths"
        listed.write_text("".join(f"{p}\n" for p in sorted(paths)))
        command, timeout_s = config.fix.verify_command, config.fix.verify_timeout
        interval = config.fix.verify_progress_interval
        result = fix.suite.run(adapter.workdir, command, timeout_s, trail,
                               base=base, paths_file=str(listed),
                               progress_s=interval)
        remaining = timeout_s - math.ceil(result.duration_s)
        return fix.baseline.label_preexisting(
            adapter.workdir, result, command, remaining, trail,
            ref=baseline_ref, paths_file=str(listed), progress_s=interval)


def _anchors(
    outcomes: list[ItemOutcome], by_id: dict[str, FixItem],
) -> dict[str, str]:
    """The anchor file behind each item this pass claimed to have fixed.

    Claimed fixes only. An item the pass deferred or declined asserted nothing
    for the suite to contradict, and pointing a red run at one would send a
    reader to the one place the pass says it did not touch.

    The outcome carries a `file` of its own and the item carries the one the
    domain rendered; the outcome's wins where it has one, because a
    reconciliation can move it and the item is fixed at the moment the pass
    was assembled.
    """
    anchors: dict[str, str] = {}
    for outcome in outcomes:
        if not outcome.outcome.counts_as_fixed:
            continue
        item = by_id.get(outcome.id)
        anchor = outcome.file or (item.file if item else "")
        if anchor:
            anchors[outcome.id] = anchor
    return anchors
