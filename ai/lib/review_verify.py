"""What a review claims, checked against the tree it claims it about.

`post_process_findings` is that check as a whole pass: a caller hands it a
finished review file and gets back what verification did to it. Evidence
checking, evidence stripping and the reconciliation are steps of that pass
rather than a menu — run in another order they leave a review whose prose
describes findings by IDs it no longer uses — so they are private and the pass
is what callers reach for.

Two gates run over a finished review and both only ever remove findings.

Evidence verification is mechanical. Every must-fix and should-fix finding
quotes the code it is about; that quote is checked against the file on disk,
and a finding whose evidence is not there is dropped. Roughly a quarter of
reviews lose at least one finding this way. The comparison is a substring test
over two sides normalized the same way — comments stripped, indentation and
blank lines collapsed — because a reviewer annotates a quote with lines the
file does not carry, and stripping only the quote leaves the file holding text
the quote no longer has, which makes a verbatim quote fail to match itself.

The disprove gate is adversarial. Each must-fix and should-fix finding is
challenged by an agent, and one that cannot survive the challenge is dropped
before the review is posted. Reading that agent's verdicts back and applying
them is here; whether the gate runs at all is `review_phases`'.

The synthesis agent wrote the ``## Summary`` and the ``## Verdict`` before
either gate ran, so both can describe findings that are no longer in the file.
Regenerating them would cost the agent's qualitative assessment, which is the
part of a review a reader cannot reconstruct from counts. So the prose stays
and the review says what left it:

* A blockquote at the end of ``## Summary`` names each dropped finding by
  severity and path — not by ID, since renumbering has already reassigned
  those — and why it was dropped.
* ``## Verdict`` is rewritten when the surviving counts no longer support the
  stated action. A drop can only remove findings, so this only ever lowers a
  verdict: ``Request changes`` → ``Needs discussion`` → ``Approve``. A verdict
  the remaining findings still support is left exactly as written, and
  ``Disapprove`` is never touched — it means the overall approach is wrong,
  which the counts do not derive, so no drop refutes it.

Both are idempotent — a review that already carries the note is left alone, so
re-running post-processing does not stack notes or re-lower a verdict.

Which verdict a tally supports in the first place is `review_document`'s, and
so is the finding-line grammar read here: `_VERIFY_FINDING_RE` is a stricter
shape over the same location vocabulary, and the two have to agree or a finding
parses one way and verifies against the other.

So is where a finding's body ends. Both gates walk the review through
`finding_spans` and remove what they drop through `drop_findings`, because two
gates that measured a finding themselves measured it differently: one of them
took the resolved finding below a dropped one out with it, and neither of them
left a `### ` sub-heading standing. `_VERIFY_FINDING_RE` selects which findings
this gate checks and reads the location it checks them against; it no longer
says where one stops.
"""

# doc-group: findings

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import log
from pr_domains import ReviewVerdict
from review_document import (
    LINE_SUFFIX, SECTION_PRIOR_FINDINGS, SECTION_SUMMARY, SECTION_VERDICT,
    SPACED_FILE, ReviewDocument, counts_prose, drop_findings,
    finding_spans, section_span, strip_sections, verdict_from_counts,
)
from review_merge import renumber_findings, strip_stable_ids
from review_types import (
    SEVERITY_MUST, SEVERITY_SHOULD, FindingSpan, severity_by_key,
)
from text import plural

# ── Evidence verification ────────────────────────────────────────────────────

_EVIDENCE_RE = re.compile(
    r">\s*```\w*\n(.*?)\n\s*>\s*```",
    re.DOTALL,
)


def _extract_evidence(body: str) -> str | None:
    m = _EVIDENCE_RE.search(body)
    if not m:
        return None
    lines = m.group(1).split("\n")
    cleaned = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("> "):
            stripped = stripped[2:]
        elif stripped.startswith(">"):
            stripped = stripped[1:]
        cleaned.append(stripped.rstrip())
    return "\n".join(ln for ln in cleaned if ln)


def _normalize_code(text: str) -> str:
    lines = text.strip().split("\n")
    return "\n".join(line.strip() for line in lines if line.strip())


_TRAILING_LINE_COMMENT_RE = re.compile(
    r"^("
    r'(?:"(?:[^"\\]|\\.)*")'       # double-quoted string
    r"|(?:'(?:[^'\\]|\\.)*')"       # single-quoted string
    r"|(?:`[^`]*`)"                 # backtick string (Go raw / JS template)
    r"|[^\"'`/#]"                   # normal chars
    r")+"                           # greedy: consume all safe content
    r"(\s+//\s.*|\s+#\s.*)$"        # trailing // or # comment
)

_TEMPLATE_COMMENT_RE = re.compile(r"\s*\{\{/\*.*?\*/\}\}\s*$")

# A line whose content is nothing but prose commentary. The space after the
# marker is what separates prose from a directive that is really code —
# `#!/usr/bin/env bash`, `#include`, `//nolint:errcheck` — which is kept, on the
# same rule the trailing form already applies.
_COMMENT_ONLY_RE = re.compile(r"^\s*(?://|#)(?:\s|$)")


def _strip_comments(text: str) -> str:
    """Drop comments from one side of the evidence match.

    A whole-line comment goes entirely: reviewers annotate evidence with lines
    that are not in the file at all, so a comment line is not something the
    match can insist on. A comment on a code line loses only its tail.

    Both sides of the comparison run through this — see `_check_fragments`.
    """
    lines = []
    for line in text.split("\n"):
        line = _TEMPLATE_COMMENT_RE.sub("", line)
        if _COMMENT_ONLY_RE.match(line):
            lines.append("")
            continue
        m = _TRAILING_LINE_COMMENT_RE.match(line)
        if m:
            line = line[:m.start(2)]
        lines.append(line)
    return "\n".join(lines)


def _find_mismatch(norm_frag: str, norm_file: str) -> dict:
    for i in range(len(norm_frag), 0, -1):
        if norm_frag[:i] in norm_file:
            return {"longest_match_prefix": i, "first_mismatch": norm_frag[i:i + 60]}
    return {"longest_match_prefix": 0, "first_mismatch": norm_frag[:60]}


def _check_fragments(evidence: str, file_content: str) -> dict:
    """Match each evidence fragment against the file it was quoted from.

    The two sides are normalized the same way — `_strip_comments` then
    `_normalize_code` — because the comparison is a substring test. Stripping
    only the quote leaves the file holding text the quote no longer has, and a
    verbatim quote then fails to match itself.
    """
    cleaned = _strip_comments(evidence)
    norm_file = _normalize_code(_strip_comments(file_content))
    fragments = re.split(r"(?m)^\s*\.\.\.\s*$", cleaned)
    fragments = [f for f in fragments if f.strip()]
    if not fragments:
        return {"match_result": True}
    result: dict = {"evidence_length": len(evidence), "fragments": len(fragments)}
    for frag in fragments:
        norm_frag = _normalize_code(frag)
        if norm_frag not in norm_file:
            result.update(_find_mismatch(norm_frag, norm_file))
            result["match_result"] = False
            return result
    result["match_result"] = True
    return result


def _match_evidence(path: str, evidence: str | None, wt_path: str) -> dict:
    resolved = Path(wt_path) / path
    detail: dict = {
        "path": path,
        "has_evidence": evidence is not None,
        "file_exists": resolved.exists(),
    }
    if evidence is None or not resolved.exists():
        detail["match_result"] = resolved.exists() and evidence is None
        return detail
    try:
        file_content = resolved.read_text()
    except OSError:
        detail["match_result"] = False
        return detail
    detail.update(_check_fragments(evidence, file_content))
    return detail


# This pattern selects: which findings this gate checks, and the location it
# checks each one against. Where a finding's body ends is not its business —
# `finding_spans` measures that, so a line this pattern cannot read ends the
# span above it instead of joining that finding's evidence.
#
# The space-free class stays exactly as it was — anything the delimiters cannot
# hold, line suffix included, which `rsplit` strips below — and the spaced
# shape is beside it rather than replacing it. Every location that parsed
# before parses the same way, since a space-free span never reaches the second
# alternative at all.
#
# `SPACED_FILE` needs the same bound it has in `review_document`'s location
# grammar, where a lookahead makes the filename account for the whole span.
# Here the closing delimiter is that bound: the extension and its optional line
# suffix have to run right up to it, so "the fix lands in v2.0 of the tool" is
# still no path and a greedy space run cannot walk past the real filename.
_VERIFY_FINDING_RE = re.compile(
    r"^- (?:\[ \] )?"
    r"\*\*\[([MSNI])(\d+)\]\*\*"
    r"\s+(?:<!-- sid:\w+ -->\s+)?"
    rf"(?:\*\*[`]?([^`*\s]+?|{SPACED_FILE}{LINE_SUFFIX})[`]?\*\*"
    rf"|[`]([^`\s]+?|{SPACED_FILE}{LINE_SUFFIX})[`])"
    rf"{LINE_SUFFIX}"
    r"\s*—\s*(.*)"
)


def _verification_body(span: FindingSpan, head: str, text: str) -> str:
    """The finding's own words: what follows the em dash, and the lines below it.

    Read off the span, so the evidence a finding is checked against is the
    evidence written under that finding and nothing written under the next.
    """
    below = [line.rstrip() for line in span.text_of(text).split("\n")[1:] if line.strip()]
    return "\n".join([head, *below] if head else below)


def _verification_finding(span: FindingSpan, text: str) -> dict | None:
    """What this gate checks about one finding, or None when it checks none.

    A declaration whose location `_VERIFY_FINDING_RE` cannot read is skipped:
    there is no path to match the evidence against, so the check has nothing to
    say about it. A declaration in the prior-findings ledger is skipped too —
    it reports the last review's finding, and the file it names was quoted
    against a commit this one is not looking at.
    """
    m = _VERIFY_FINDING_RE.match(span.line)
    if not m or span.reported:
        return None
    raw_path = (m.group(3) or m.group(4) or "").replace("\\_", "_")
    return {
        "id": f"{m.group(1)}{m.group(2)}",
        "severity": m.group(1),
        "path": raw_path.rsplit(":", 1)[0] if ":" in raw_path else raw_path,
        "body": _verification_body(span, m.group(5), text),
    }


def _parse_findings_for_verification(text: str) -> list[dict]:
    checked = (_verification_finding(span, text) for span in finding_spans(text))
    return [finding for finding in checked if finding]


def _verification_detail(
    finding: dict, evidence: str | None, wt_path: str,
) -> dict:
    detail = _match_evidence(finding["path"], evidence, wt_path)
    detail["id"] = finding["id"]
    detail["severity"] = finding["severity"]
    return detail


def _drop_reason(detail: dict) -> str:
    reason = "file not found" if not detail["file_exists"] else "evidence mismatch"
    if detail.get("longest_match_prefix") is not None:
        reason += f" at char {detail['longest_match_prefix']}"
    return reason


def _verify_findings(text: str, wt_path: str) -> tuple[str, dict]:
    findings = _parse_findings_for_verification(text)
    dropped: list[str] = []
    details: list[dict] = []
    for f in findings:
        if f["severity"] not in (SEVERITY_MUST, SEVERITY_SHOULD):
            continue
        evidence = _extract_evidence(f["body"])
        detail = _verification_detail(f, evidence, wt_path)
        details.append(detail)
        if not detail["match_result"]:
            dropped.append(f["id"])
            log.info(f"Dropping {f['id']} ({f['path']}): {_drop_reason(detail)}")
    result = {
        "findings_checked": len(details),
        "findings_passed": len(details) - len(dropped),
        "findings_dropped": len(dropped),
        "dropped": dropped,
        "details": details,
    }
    if dropped:
        text = drop_findings(text, dropped)
    return text, result


_EVIDENCE_BLOCK_START_RE = re.compile(r"^ {2,}>\s*```")
_EVIDENCE_BLOCKQUOTE_RE = re.compile(r"^ {2,}>")


def _strip_evidence_blocks(text: str) -> str:
    lines = text.split("\n")
    kept: list[str] = []
    in_evidence = False
    for line in lines:
        is_fence = _EVIDENCE_BLOCK_START_RE.match(line)
        if not in_evidence and is_fence:
            in_evidence = True
            continue
        if in_evidence:
            in_evidence = not is_fence
            continue
        if _EVIDENCE_BLOCKQUOTE_RE.match(line):
            continue
        kept.append(line)
    return "\n".join(kept)


# ── Reconciling prose against dropped findings ───────────────────────────────

_DROP_NOTE_MARKER = "<!-- verification-drops -->"


def _drop_note(details: list[dict], dropped: list[str]) -> str:
    """A blockquote naming every dropped finding and why it went.

    Identified by severity and path rather than by ID: `renumber_findings` runs
    after the drop, so the IDs the verification recorded no longer point at the
    findings that survived.
    """
    dropped_set = set(dropped)
    lines = [
        f"> - {severity_by_key(d['severity']).section} — `{d['path']}`: {_drop_reason(d)}"
        for d in details
        if d["id"] in dropped_set
    ]
    n = len(lines)
    header = f"> **Evidence verification removed {n} finding{plural(n)}:**"
    return "\n".join([_DROP_NOTE_MARKER, header, *lines])


def _insert_drop_note(text: str, note: str) -> str:
    span = section_span(text, SECTION_SUMMARY)
    if span is None:
        # No Summary to correct — the mechanical paths build theirs from this
        # text afterwards, so the note goes above the findings it explains and
        # ends up directly beneath the summary they generate.
        first = re.search(r"^## ", text, re.MULTILINE)
        if first is None:
            return f"{text.rstrip()}\n\n{note}\n"
        return f"{text[:first.start()]}{note}\n\n{text[first.start():]}"

    body = span.body_of(text).rstrip()
    return (
        f"{text[:span.start]}{body}\n\n{note}\n\n{text[span.end:].lstrip(chr(10))}"
    )


def _revise_verdict(text: str, counts: dict[str, int], n_dropped: int) -> str:
    """Lower a verdict the surviving findings no longer support.

    Dropping only ever removes findings, so a stale verdict can only overstate
    — the revision lowers, never raises, and leaves an unranked verdict alone.
    """
    span = section_span(text, SECTION_VERDICT)
    if span is None:
        return text

    stated = ReviewVerdict.stated_in(span.body_of(text))
    supported = verdict_from_counts(counts)
    if not stated or not stated.outranks(supported):
        return text

    revised = (
        f"{supported.prose} — {counts_prose(counts) or 'no findings'} after "
        f"evidence verification removed {n_dropped} finding{plural(n_dropped)}.\n"
    )
    return f"{text[:span.start]}\n{revised}\n{text[span.end:].lstrip(chr(10))}"


def _reconcile_dropped_findings(text: str, verification: dict) -> str:
    """Make a review's prose account for the findings verification removed.

    Runs after renumbering, so the counts it reads are the ones the finished
    file reports.
    """
    dropped = verification.get("dropped") or []
    if not dropped or _DROP_NOTE_MARKER in text:
        return text

    text = _revise_verdict(text, ReviewDocument(body=text).open_counts, len(dropped))
    return _insert_drop_note(text, _drop_note(verification.get("details") or [], dropped))


# ── Disprove-it gate ─────────────────────────────────────────────────────────


@dataclass
class DisproveResult:
    finding_id: str
    verdict: str  # "SURVIVES" or "FALSIFIED"
    reason: str


_RESULT_RE = re.compile(
    r"^- \[([A-Z]\d+)\]\s+(SURVIVES|FALSIFIED)\s*(?:—|--)\s*(.+)",
)


def parse_disprove_output(text: str) -> list[DisproveResult]:
    results: list[DisproveResult] = []
    for line in text.splitlines():
        m = _RESULT_RE.match(line.strip())
        if m:
            results.append(DisproveResult(
                finding_id=m.group(1),
                verdict=m.group(2),
                reason=m.group(3).strip(),
            ))
    return results


def apply_disprove_results(
    review_text: str, results: list[DisproveResult],
) -> tuple[str, dict]:
    falsified_ids = {
        r.finding_id for r in results if r.verdict == "FALSIFIED"
    }
    reason_map = {r.finding_id: r.reason for r in results if r.verdict == "FALSIFIED"}

    if not falsified_ids:
        return review_text, {
            "total_challenged": len(results),
            "survived": len(results),
            "falsified": 0,
            "falsified_ids": [],
            "reasons": {},
        }

    survived = len(results) - len(falsified_ids)
    return drop_findings(review_text, falsified_ids), {
        "total_challenged": len(results),
        "survived": survived,
        "falsified": len(falsified_ids),
        "falsified_ids": sorted(falsified_ids),
        "reasons": reason_map,
    }


# ── The whole pass over a finished review ────────────────────────────────────

def post_process_findings(review_file: str, wt_path: str = "") -> dict | None:
    """Every gate and cleanup a finished review needs, run over it in place.

    The verification report, or None when `review_file` does not exist — the
    guard covers all of the sub-steps, so a caller gets no partial result from
    a review that was never written. Without `wt_path` there is no tree to
    check evidence against, so verification is skipped and only the cleanups
    run; the report is None then too.

    The order is the contract rather than an implementation detail. The prior
    findings ledger goes before renumbering because its IDs number the
    *previous* review — left in, they both mislead a reader and skew the
    renumbering of the findings this review actually has. The reconciliation
    goes after, because it reads the counts the finished file reports and must
    not name findings by IDs renumbering has since reassigned.
    """
    path = Path(review_file)
    if not path.exists():
        return None
    text = path.read_text()
    verification: dict | None = None
    if wt_path:
        text, verification = _verify_findings(text, wt_path)
        dropped = verification["dropped"]
        if dropped:
            log.info(f"Dropped {len(dropped)} unverified findings: {', '.join(dropped)}")
    text = _strip_evidence_blocks(text)
    text = strip_stable_ids(text)
    text = strip_sections(text, [SECTION_PRIOR_FINDINGS])
    text = renumber_findings(text)
    if verification:
        text = _reconcile_dropped_findings(text, verification)
    path.write_text(text)
    return verification
