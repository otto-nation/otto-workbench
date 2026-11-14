"""Checklists the plan, implement and escalate stages answer in.

Reuses :mod:`fix.tracking`'s box type and parser so the three vocabularies
cannot drift from the format the engine already reads. An untouched item still
reads as ``DEFERRED``.
"""

# doc-group: pipeline

from __future__ import annotations

import re
from pathlib import Path

import fix.tracking
from fix.plan_model import (
    CHANGE_CAP, PLAN_SCHEMA, ItemPlan, PlanMode, PlanVerdict, classify_paths,
    item_digest, unsafe_relpath,
)
from fix.tracking import _Box, _SECTION_RE, _ticked_reasons, box_pattern
from fix.types import FixItem
from pr.fix import Escalation, FixOutcome, Handoff, ItemOutcome

PLAN_BOXES: tuple[_Box, ...] = (
    _Box("fix", FixOutcome.FIXED,
         "you know what to change. Fill Files, Change and Test below",
         asks=False),
    _Box("already addressed", FixOutcome.ALREADY_ADDRESSED,
         "the code already does this. Replace `<why>` with file:line"),
    _Box("dismiss", FixOutcome.DISMISSED,
         "the {noun} should not be acted on. Replace `<why>` with why"),
    _Box("needs a person", FixOutcome.NEEDS_HUMAN,
         "the call is not yours. Replace `<why>` with what it turns on"),
)

IMPLEMENT_BOXES: tuple[_Box, ...] = (
    _Box("fixed", FixOutcome.FIXED,
         "you applied the plan. Replace `<why>` with the test that fails "
         "without the change"),
    _Box("plan failed", FixOutcome.NEEDS_HUMAN,
         "the code does not match the plan. Replace `<why>` with what "
         "contradicts it, and revert edits for this item"),
)

ESCALATE_BOXES: tuple[_Box, ...] = (
    _Box("fixed", FixOutcome.FIXED,
         "you fixed it. Replace `<why>` with the test that fails without it"),
    _Box("needs a person", FixOutcome.NEEDS_HUMAN,
         "it still needs the operator. Replace `<why>` with one sentence "
         "the reviewer-facing row prints"),
)

_PLAN_OUTCOME = {
    "fix": PlanVerdict.FIX,
    "already addressed": PlanVerdict.ALREADY_ADDRESSED,
    "dismiss": PlanVerdict.DISMISS,
    "needs a person": PlanVerdict.NEEDS_HUMAN,
}

_FIELD_RE = re.compile(r"^(Files|Change|Test|Analysis):\s*(.*)$")
_BOX_LINE_RE = re.compile(r"^- \[[ xX]\] ")
_PLAN_BOX_RE = box_pattern(PLAN_BOXES)


def _fields(body: str) -> dict[str, str]:
    """Labelled fields in `body`, each running to the next field, box, or end."""
    found: dict[str, str] = {}
    lines = body.splitlines()
    i = 0
    while i < len(lines):
        match = _FIELD_RE.match(lines[i])
        if not match:
            i += 1
            continue
        name, first = match.group(1), match.group(2)
        chunks = [first] if first.strip() else []
        i += 1
        while i < len(lines):
            if _FIELD_RE.match(lines[i]) or _BOX_LINE_RE.match(lines[i]):
                break
            chunks.append(lines[i])
            i += 1
        found[name] = "\n".join(chunks).strip()
    return found


def _file_list(raw: str) -> tuple[str, ...]:
    names: list[str] = []
    for line in raw.replace(",", "\n").splitlines():
        name = line.strip().strip("- ").strip()
        if name:
            names.append(name)
    return tuple(names)


def parse_plan(
    path: Path,
    items: list[FixItem],
    *,
    mode: PlanMode = PlanMode.ACT,
    head_sha: str = "",
    model: str = "",
    pr_files: frozenset[str] | None = None,
) -> dict[str, ItemPlan]:
    """Plans keyed by item id. Unticked sections are absent."""
    if not path.exists():
        return {}
    by_id = {item.id: item for item in items}
    text = path.read_text()
    matches = list(_SECTION_RE.finditer(text))
    plans: dict[str, ItemPlan] = {}
    for n, match in enumerate(matches):
        end = matches[n + 1].start() if n + 1 < len(matches) else len(text)
        item_id = match.group("id")
        body = text[match.end():end]
        ticked = _ticked_reasons(body, _PLAN_BOX_RE)
        verdict = None
        reason = ""
        for box in PLAN_BOXES:
            if box.label in ticked:
                verdict = _PLAN_OUTCOME[box.label]
                reason = ticked[box.label]
                break
        if verdict is None:
            continue
        fields = _fields(body)
        files = _file_list(fields.get("Files", ""))
        change = fields.get("Change", "")[:CHANGE_CAP]
        test = fields.get("Test", "")
        evidence_file, evidence_line = "", 0
        if verdict is PlanVerdict.ALREADY_ADDRESSED and ":" in reason:
            loc, _, _rest = reason.partition(" — ")
            loc = loc.strip()
            if ":" in loc:
                evidence_file, _, line_s = loc.rpartition(":")
                if line_s.isdigit():
                    evidence_line = int(line_s)
        _rejected, outside = classify_paths(files, pr_files)
        source = by_id.get(item_id)
        plans[item_id] = ItemPlan(
            schema=PLAN_SCHEMA,
            item_id=item_id,
            verdict=verdict,
            mode=mode,
            reason=reason,
            files=files,
            change=change,
            test=test,
            evidence_file=evidence_file,
            evidence_line=evidence_line,
            outside_pr=outside,
            item_digest=item_digest(source) if source else "",
            head_sha=head_sha,
            model=model,
        )
    return plans


def parse_implement(path: Path) -> list[ItemOutcome]:
    """Implement ticks: `plan failed` is NEEDS_HUMAN with PLAN_FAILED."""
    outcomes = fix.tracking.parse(path, IMPLEMENT_BOXES)
    for outcome in outcomes:
        if outcome.outcome is FixOutcome.NEEDS_HUMAN:
            outcome.handoff = Handoff.PLAN_FAILED
    return outcomes


def parse_escalate(path: Path) -> list[ItemOutcome]:
    """Escalate ticks plus the Analysis field as `escalation_note`."""
    if not path.exists():
        return []
    outcomes = fix.tracking.parse(path, ESCALATE_BOXES)
    text = path.read_text()
    matches = list(_SECTION_RE.finditer(text))
    notes: dict[str, str] = {}
    for n, match in enumerate(matches):
        end = matches[n + 1].start() if n + 1 < len(matches) else len(text)
        notes[match.group("id")] = _fields(text[match.end():end]).get("Analysis", "")
    for outcome in outcomes:
        outcome.escalation_note = notes.get(outcome.id, "")
        if outcome.outcome is FixOutcome.FIXED:
            outcome.escalation = Escalation.RESOLVED
        elif outcome.outcome is FixOutcome.NEEDS_HUMAN:
            outcome.escalation = Escalation.CONFIRMED
        else:
            outcome.escalation = Escalation.UNANSWERED
    return outcomes


def render_plan_body(plan: ItemPlan) -> str:
    """What the implementer is shown for one item — the plan, nothing else."""
    files = ", ".join(plan.files)
    return (
        f"Verdict: {plan.verdict.value}\n"
        f"Reason: {plan.reason}\n"
        f"Files: {files}\n"
        f"Change: {plan.change}\n"
        f"Test: {plan.test}\n"
    )


def plan_is_usable(plan: ItemPlan) -> bool:
    """A plan the implementer may be handed: valid, and no unsafe path."""
    if not plan.valid:
        return False
    return not any(unsafe_relpath(path) for path in plan.files)
