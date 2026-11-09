"""The prompt shows the agent `example()` as `core.serde.to_dict` writes
it, followed by `FIELD_NOTES` for every field, so what the prompt asks for and
what `review.ingest` accepts are one schema. A test holds the notes to the
fields.

`output_format` is that rendering. `CARRY_FIELDS` (sid, declined,
prior_findings) are hidden on a first-round prompt. `DERIVE_FIELDS` (title,
rationale, paths on a FocusReport) are shown only when the Focus section asks
the writer to add areas.
"""

# doc-group: findings

from __future__ import annotations

import dataclasses
import json
from dataclasses import replace
from enum import Enum
from typing import get_type_hints

import core.serde
from core.phases import Mode
from core.serde import HintKind, classify
from core.text import join_or
from review.artifacts import (
    AgentFinding, AgentQuestion,
    AgentFocus,
    BehaviourReport,
    ClaimReport,
    DisproveArtifact,
    DisproveVerdict,
    FocusReport,
    GroupArtifact,
    HolisticArtifact,
    Lead,
    LedgerLine,
    QuietFile,
    ReviewArtifact,
    ScoutArtifact,
    TriageEntry, VerifiedNote,
)
from review.budget import MAX_QUESTIONS, MAX_VERIFIED
from review.types import SEVERITIES

_SCAN_FOCUS_NOTE = (
    "1–3 areas this PR most needs reviewed — the risks particular to this "
    "change, each with a title, a rationale, and the paths it concerns"
)


# ── What the prompt says about each field ───────────────────────────────────

_SEVERITY_LEGEND = "; ".join(f"{s.key} = {s.section}" for s in SEVERITIES)
_LEDGER_NOTE = (
    "only when the prompt has a `## Prior review` section: one entry for EVERY prior "
    "finding shown there; [] otherwise"
)
_QUESTIONS_NOTE = (
    "what only the author can answer — intent, or context the diff does not show; "
    f"at most {MAX_QUESTIONS}; usually []. A question whose answer could reveal a defect is a finding")
_VERIFIED_NOTE = (
    f"what you checked and found sound, one entry each; at most {MAX_VERIFIED}; [] when none. "
    "Never a finding that ends \"acceptable\" or \"not a defect\"")

FIELD_NOTES: dict[type, dict[str, str]] = {
    AgentFinding: {
        "id": f"a severity key and a number from 1 ({_SEVERITY_LEGEND}); numbers restart "
              "per severity, never repeat within one, and are renumbered after you write",
        "path": "the file, relative to the repository root, with no `:line` on it; null for "
                "a finding about the PR as a whole",
        "line": "the first line it is about; null for the whole file",
        "end_line": "the last line of a range; null for a single line",
        "body": "markdown: the first line states the defect, later lines explain it and the fix",
        "evidence": "must-fix and should-fix: 1–5 lines copied verbatim from `path` at `line` "
                    "— the code itself, with no `>` markers and no fence; \"\" for nits and idioms",
        "sid": "carrying a prior finding forward: the value in its `<!-- sid: … -->` marker, "
               "or the `sid` the merged findings show it with (synthesis sees one on every "
               "finding, and keeps it); \"\" for a finding you raise yourself",
        "declined": "carrying forward a finding the prior review records as declined, or one "
                    "you decline on the merits: its one-line reason, so it is not raised or "
                    "auto-fixed again. Needs `sid`; \"\" otherwise",
    },
    TriageEntry: {
        "path": "a file you were asked to review",
        "note": "its tier and what you found there, on one line",
    },
    LedgerLine: {
        "id": "the prior finding's ID, copied exactly",
        "disposition": "what became of it",
        "path": "the prior finding's path, copied exactly; null if it named none",
        "note": "any detail; the verdict is `disposition`, never the note",
    },
    GroupArtifact: {
        "triage": "one entry per file in your group",
        "findings": "your findings, any severity, in any order",
        "prior_findings": _LEDGER_NOTE,
        "behaviours": "each behaviour change in your files, one per entry; [] when the "
                       "change alters no behaviour",
        "questions": _QUESTIONS_NOTE, "verified": _VERIFIED_NOTE,
    },
    ReviewArtifact: {
        "summary": "markdown: what the change does and its overall quality",
        "findings": "every finding, any severity, in any order",
        "triage": "a PR review: one entry per changed file; a self-review: []",
        "prior_findings": _LEDGER_NOTE,
        "verdict": "a PR review: the verdict the task asks for, then why; a self-review: \"\"",
        "focus": "one entry for EVERY area in the prompt's Focus section, by its `id`; "
                  "add your own with id \"\" only where that section asks you to",
        "claims": "every distinct claim the Description or the Related issue (title, body, "
                   "acceptance criteria) makes that the code could confirm or refute; [] "
                   "when nothing there is checkable",
        "behaviours": "every behaviour change in the PR — on synthesis, the merged groups' "
                       "lists with duplicates merged; at most 12; fold related behaviours "
                       "together; [] when nothing changes behaviour",
        "questions": _QUESTIONS_NOTE, "verified": _VERIFIED_NOTE,
    },
    Lead: {
        "path": "the file the lead is about",
        "line": "the line it is about; null for the whole file",
        "end_line": "the last line of a range; null for a single line",
        "concern": "what looks wrong, in one sentence",
        "severity_hint": "how serious you think it is; the deep reviewer decides",
        "why": "why it matters, in a sentence",
    },
    QuietFile: {
        "path": "a file that needs no scrutiny",
        "reason": "why, in a few words: generated, renamed, formatting-only",
    },
    AgentFocus: {
        "title": "what this area is, in a few words",
        "rationale": "why it needs review",
        "paths": "the files or directories it concerns; [] when it spans the PR",
    },
    ScoutArtifact: {
        "leads": "one per concern; [] if nothing looks suspicious",
        "no_scrutiny": "files deep reviewers can skip",
        "direction_concern": "only if the approach itself is wrong: why, and what to do "
                             "instead; \"\" otherwise",
        "focus": _SCAN_FOCUS_NOTE,
    },
    HolisticArtifact: {
        "assessment": "markdown: whether the PR does what it claims, and whether its scope "
                      "is focused",
        "flags": "things the detailed reviewers should watch for, one per entry",
        "cross_module": "cross-module design issues, one per entry",
        "direction_concern": "only if the approach itself is wrong: why, and what to do "
                             "instead; \"\" otherwise",
        "focus": _SCAN_FOCUS_NOTE,
    },
    DisproveVerdict: {
        "id": "a must-fix or should-fix finding ID from the review",
        "outcome": "whether it survived your attempt to falsify it",
        "reason": "concrete: for a falsified finding, the file and line that disproves it",
    },
    DisproveArtifact: {
        "verdicts": "one per finding you challenged; a finding left out survives",
    },
}
FIELD_NOTES[AgentQuestion] = {
    "path": "the file it is about, with no `:line`; null for the PR as a whole",
    "line": "the line; null for the whole file",
    "body": "the question, in a sentence or two"}
FIELD_NOTES[VerifiedNote] = {
    "path": "the file it is about, with no `:line`; null for the PR as a whole",
    "line": "the line; null for the whole file",
    "body": "what you checked and why it holds, in a sentence"}
FIELD_NOTES[FocusReport] = {
    "id": "the area's id from the Focus section, or \"\" for one you add",
    "coverage": "what you checked and what you found, citing finding IDs",
    **{k: FIELD_NOTES[AgentFocus][k] for k in ("title", "rationale", "paths")},
}
FIELD_NOTES[BehaviourReport] = {
    "text": "the behaviour, in one sentence",
    "test_cited": "the test that exercises it as `path` or `path::name`, checked after "
                   "you write; \"\" when you found none",
    "paths": "the changed files it lives in, at least one",
}
FIELD_NOTES[ClaimReport] = {
    "source": '"description" for the Description and "ticket" for the Related issue',
    "text": "the claim, in the author's words",
    "verdict": "supported holds; contradicted is shown false; unsupported is checkable but "
               "nothing in the code shows it; unverifiable cannot be checked statically, or "
               "its cited code did not verify",
    "evidence": "prose that says why the verdict is that",
    "path": "required for supported and contradicted: the file that shows the verdict; "
            "checked after you write; otherwise \"\"",
    "snippet": "required for supported and contradicted: a verbatim snippet from that file "
               "that shows the verdict; checked after you write; otherwise \"\"",
    "finding": "the id of your finding that covers this claim, if one does; otherwise \"\"",
}

# Fields an example leaves at their default on purpose: showing a filled-in
# direction concern would invite one on every run.
EXAMPLE_LEAVES_EMPTY: dict[type, frozenset[str]] = {
    ScoutArtifact: frozenset({"direction_concern"}),
    HolisticArtifact: frozenset({"direction_concern"}),
    ClaimReport: frozenset({"finding"}),
}


def _nested_dataclass(hint) -> type | None:
    """The dataclass a list or tuple field holds, or None."""
    kind, args = classify(hint)
    if kind is HintKind.OPTIONAL:
        kind, args = classify(args[0])
    if kind in (HintKind.TUPLE, HintKind.LIST) and args and dataclasses.is_dataclass(args[0]):
        return args[0]
    return None


def _enum_values(hint) -> str:
    if not (isinstance(hint, type) and issubclass(hint, Enum)):
        return ""
    return f" — one of {join_or([json.dumps(m.value) for m in hint])}"


# The fields only a re-review fills: what carries a prior finding forward. A
# first-round prompt neither shows nor explains them (D20), so an agent with
# no prior review in front of it is never invited to invent a sid or a decline.
# Each has a default, so a document without them is still a valid artifact.
CARRY_FIELDS: dict[type, frozenset[str]] = {
    AgentFinding: frozenset({"sid", "declined"}),
    GroupArtifact: frozenset({"prior_findings"}),
    ReviewArtifact: frozenset({"prior_findings"}),
}
DERIVE_FIELDS: dict[type, frozenset[str]] = {FocusReport: frozenset({"title", "rationale", "paths"})}


def _focus_example(
    entries: tuple[FocusReport, ...], *, derive_focus: bool, has_areas: bool,
) -> tuple[FocusReport, ...]:
    return tuple(e for e in entries if (e.id == "" and derive_focus) or (e.id != "" and has_areas))


def _shown_fields(kind: type, hidden: dict[type, frozenset[str]]) -> list:
    """`kind`'s fields a prompt shows, each with its type hint."""
    hints = get_type_hints(kind)
    skip = hidden.get(kind, frozenset())
    return [(f.name, hints[f.name]) for f in dataclasses.fields(kind) if f.name not in skip]


def _example_dict(kind: type, data: dict, hidden: dict[type, frozenset[str]]) -> dict:
    """`data` (a `to_dict` of a `kind`) without the fields `hidden` leaves out, at any depth."""
    out = {}
    for name, hint in _shown_fields(kind, hidden):
        nested = _nested_dataclass(hint)
        value = data[name]
        out[name] = [_example_dict(nested, item, hidden) for item in value] if nested else value
    return out


def _field_guide(kind: type, hidden: dict[type, frozenset[str]], prefix: str = "") -> list[str]:
    lines = []
    for name, hint in _shown_fields(kind, hidden):
        lines.append(f"- `{prefix}{name}`: {FIELD_NOTES[kind][name]}{_enum_values(hint)}")
        nested = _nested_dataclass(hint)
        if nested is not None:
            lines += _field_guide(nested, hidden, f"{prefix}{name}[].")
    return lines


def output_format(kind: type, mode: Mode, *, rereview: bool,
                  derive_focus: bool = False, has_areas: bool = True,
                  has_claim_source: bool = True,
                  has_behaviour_source: bool = True) -> str:
    """What a prompt's Output section shows: the example, then every field's meaning.

    `rereview` is whether the prompt carries prior findings to account for;
    without it the carry fields (`CARRY_FIELDS`) are neither shown nor explained.
    `has_areas` defaults to true so a format rendered outside a run shows the
    covering example. `has_claim_source` defaults to true so the example shows
    a claim; without a source the example's claims are []. `has_behaviour_source`
    defaults to true so the example shows a behaviour; without a source the
    example's behaviours are [].
    """
    hidden = {**(CARRY_FIELDS if not rereview else {}), **({} if derive_focus else DERIVE_FIELDS)}
    example = kind.example(mode)
    if kind is ReviewArtifact:
        example = replace(example, focus=_focus_example(
            example.focus, derive_focus=derive_focus, has_areas=has_areas))
        if not has_claim_source:
            example = replace(example, claims=())
    if kind in (GroupArtifact, ReviewArtifact) and not has_behaviour_source:
        example = replace(example, behaviours=())
    shown = _example_dict(kind, core.serde.to_dict(example), hidden)
    return (
        "The file holds ONE JSON document — nothing before or after it, no code fence, "
        "no commentary — shaped like this example, with your own content in place of "
        "the example's. Write \"\" or [] where a field's note says so.\n\n"
        f"```json\n{json.dumps(shown, indent=2)}\n```\n\n"
        "What each field holds:\n" + "\n".join(_field_guide(kind, hidden))
    )
