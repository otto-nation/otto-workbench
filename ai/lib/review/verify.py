"""What a review claims, checked against the tree it claims it about.

`post_process_record` is that check as a whole pass: a caller hands it a
finished review record and gets back what verification did to it. Evidence
checking and the reconciliation are steps of that pass rather than a menu —
run in another order they leave a review whose prose describes findings by
IDs it no longer uses — so they are private and the pass is what callers
reach for.

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
challenged by an agent, and one that cannot survive the challenge is marked
`FALSIFIED` on the record — kept, never rendered. Reading that agent's
verdicts back and applying them is `apply_disprove`; whether the gate runs at
all is `review.phases`'.

The synthesis agent wrote the Summary and the Verdict before either gate ran,
so both can describe findings that are no longer visible. Regenerating them
would cost the agent's qualitative assessment, which is the part of a review
a reader cannot reconstruct from counts. So the prose stays and the review
says what left it:

* A blockquote at the end of the Summary names each dropped finding by
  severity and path — not by ID, since renumbering has already reassigned
  those — and why it was dropped.
* The Verdict is rewritten when the surviving counts no longer support the
  stated action. A drop can only remove findings, so this only ever lowers a
  verdict: ``Request changes`` → ``Needs discussion`` → ``Approve``. A verdict
  the remaining findings still support is left exactly as written, and
  ``Disapprove`` is never touched — it means the overall approach is wrong,
  which the counts do not derive, so no drop refutes it.

Both are idempotent — a review that already carries the note is left alone, so
re-running post-processing does not stack notes or re-lower a verdict.

Which verdict a tally supports in the first place is `review.verdict`'s. The
finding-line grammar read here is `review.grammar`'s: `VERIFY_FINDING_RE` is a
stricter shape over the same vocabulary that selects which findings this gate
checks. It does not read the location: the path comes from the record, the
same reading the poster uses, and is resolved with the poster's
`review.format.resolve_path` — against the files the diff changed first, as
the poster does, and the tracked tree only when the diff has no match — so a
finding is not placed against one file and verified against another.
"""

# doc-group: findings

from __future__ import annotations

import functools
import re
from collections.abc import Callable, Collection
from dataclasses import dataclass, replace
from pathlib import Path

import core.log
import git.client
from pr.domains import ReviewVerdict
from review.grammar import (
    VERIFY_FINDING_RE, strip_line_suffix,
)
from review.format import resolve_path
from review.merge import renumber_record
from review.record import Disposition, RecordFinding, ReviewRecord
from review.render import finding_line
from review.types import (
    SEVERITY_MUST, SEVERITY_SHOULD, severity_by_key,
)
from review.verdict import counts_prose, verdict_from_counts
from core.text import plural

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


def _no_tracked_files() -> Collection[str]:
    return ()


def _tracked_files(wt_path: str) -> frozenset[str]:
    """The worktree's tracked paths, or none when there is no worktree to ask.

    A directory that is not there is not a failure of the review: the literal
    lookup already missed, and spawning git with it as the cwd would raise out
    of post-processing and cost the run its review. Not a repo answers empty
    the same way, through `git.client.lines`.
    """
    if not Path(wt_path).is_dir():
        return frozenset()
    return frozenset(git.client.lines("ls-files", cwd=wt_path))


def _locate(
    path: str, wt_path: str, tracked: Callable[[], Collection[str]],
    changed: Collection[str] = (),
) -> Path | None:
    """The file `path` names in the worktree, or None when it names none.

    The poster's own resolution first, against the files the diff changed: the
    poster has no literal-on-disk step, so a literal lookup ahead of it can pick
    a different file. With `pkg/README.md` changed and an untouched root
    `README.md` on disk, `README.md:5` posts on `pkg/README.md` and must be
    verified against it. An exact path is covered by `resolve_path`'s own
    `path in hunks`. Likewise `handler.go:42` is unique in a diff that changed
    `pkg/handler.go`, though ambiguous in a tree that also tracks
    `other/handler.go`.

    Then the literal path, then the tracked tree: the fallbacks for a path the
    diff does not name, or for a caller that has no diff.

    `tracked` is a thunk because listing the tree is a subprocess, and nearly
    every location is already repo-relative and resolves literally.
    """
    match = resolve_path(path, changed) if changed else None
    # The diff's file list includes files the PR deleted, so a match there is
    # taken only when it is on disk; otherwise the finding falls through to
    # "file not found" rather than passing as a file that exists.
    if match is not None and (Path(wt_path) / match).exists():
        return Path(wt_path) / match
    literal = Path(wt_path) / path
    if literal.exists():
        return literal
    match = resolve_path(path, tracked())
    return Path(wt_path) / match if match else None


def _match_evidence(
    path: str, evidence: str | None, wt_path: str,
    tracked: Callable[[], Collection[str]] = _no_tracked_files,
    changed: Collection[str] = (),
) -> dict:
    resolved = _locate(path, wt_path, tracked, changed)
    detail: dict = {
        "path": path,
        "has_evidence": evidence is not None,
        "file_exists": resolved is not None,
    }
    if resolved is not None and resolved != Path(wt_path) / path:
        detail["resolved_path"] = str(resolved.relative_to(wt_path))
    if evidence is None or resolved is None:
        detail["match_result"] = resolved is not None and evidence is None
        return detail
    # A file that will not decode costs this one finding its evidence, not the
    # run: `UnicodeDecodeError` is a `ValueError`, so an `OSError`-only guard
    # let a latin-1 script or a binary asset in the diff kill post-processing
    # and lose the whole review. Not `errors="replace"` — a binary's fragments
    # match nothing anyway, and False already means "could not read it to
    # verify".
    try:
        file_content = resolved.read_text()
    except (OSError, UnicodeDecodeError):
        detail["match_result"] = False
        return detail
    detail.update(_check_fragments(evidence, file_content))
    return detail


def _verification_detail(
    finding: dict, evidence: str | None, wt_path: str,
    tracked: Callable[[], Collection[str]] = _no_tracked_files,
    changed: Collection[str] = (),
) -> dict:
    detail = _match_evidence(finding["path"], evidence, wt_path, tracked, changed)
    detail["id"] = finding["id"]
    detail["severity"] = finding["severity"]
    return detail


def _drop_reason(detail: dict) -> str:
    reason = "file not found" if not detail["file_exists"] else "evidence mismatch"
    if detail.get("longest_match_prefix") is not None:
        reason += f" at char {detail['longest_match_prefix']}"
    return reason


@dataclass(frozen=True)
class PostProcessed:
    record: ReviewRecord
    verification: dict | None


def _verification_text(f: RecordFinding, head: str) -> str:
    """The finding's words and its evidence, in the shape `_extract_evidence` reads."""
    rest = f.body.split("\n")[1:]
    below = [line.rstrip() for line in [*rest, *f.evidence.split("\n")] if line.strip()]
    return "\n".join([head, *below] if head else below)


def _verify_record(record: ReviewRecord, wt_path: str, changed: Collection[str]) -> PostProcessed:
    dropped: list[str] = []
    details: list[dict] = []
    tracked = functools.cache(lambda: _tracked_files(wt_path))
    for f in record.visible:
        if f.severity not in (SEVERITY_MUST, SEVERITY_SHOULD):
            continue
        m = VERIFY_FINDING_RE.match(finding_line(f, sids=True).split("\n", 1)[0])
        if not m:
            continue
        written = strip_line_suffix((m.group(3) or m.group(4) or "").replace("\\_", "_"))
        finding = {"id": f.id, "severity": f.severity, "path": f.path or written}
        evidence = _extract_evidence(_verification_text(f, m.group(5)))
        detail = _verification_detail(finding, evidence, wt_path, tracked, changed)
        details.append(detail)
        if not detail["match_result"]:
            dropped.append(f.id)
            core.log.info(f"Dropping {f.id} ({finding['path']}): {_drop_reason(detail)}")
    gone = set(dropped)
    kept = tuple(f for f in record.findings if not (f.visible and f.id in gone))
    return PostProcessed(replace(record, findings=kept), {
        "findings_checked": len(details), "findings_passed": len(details) - len(dropped),
        "findings_dropped": len(dropped), "dropped": dropped, "details": details,
    })


# ── Reconciling prose against dropped findings ───────────────────────────────

_DROP_NOTE_MARKER = "<!-- verification-drops -->"


def _drop_note(details: list[dict], dropped: list[str]) -> str:
    """A blockquote naming every dropped finding and why it went.

    Identified by severity and path rather than by ID: `renumber_record` runs
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


def _revised_verdict(verdict: str, counts: dict[str, int], n_dropped: int) -> str:
    stated = ReviewVerdict.stated_in(verdict)
    supported = verdict_from_counts(counts)
    if not stated or not stated.outranks(supported):
        return verdict
    return (f"{supported.prose} — {counts_prose(counts) or 'no findings'} after "
            f"evidence verification removed {n_dropped} finding{plural(n_dropped)}.")


def _reconciled_record(record: ReviewRecord, verification: dict) -> ReviewRecord:
    dropped = verification.get("dropped") or []
    if not dropped or _DROP_NOTE_MARKER in record.summary:
        return record
    note = _drop_note(verification.get("details") or [], dropped)
    summary = f"{record.summary.rstrip()}\n\n{note}" if record.summary else note
    return replace(record, summary=summary,
                   verdict=_revised_verdict(record.verdict, record.open_counts, len(dropped)))


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


@dataclass(frozen=True)
class DisproveApplied:
    record: ReviewRecord
    summary: dict


def apply_disprove(record: ReviewRecord, results: list[DisproveResult]) -> DisproveApplied:
    """Mark each finding the gate falsified; kept on the record, never rendered."""
    falsified = {r.finding_id for r in results if r.verdict == "FALSIFIED"}
    reasons = {r.finding_id: r.reason for r in results if r.verdict == "FALSIFIED"}
    marked = tuple(
        replace(f, disposition=Disposition.FALSIFIED) if f.visible and f.id in falsified else f
        for f in record.findings
    )
    return DisproveApplied(replace(record, findings=marked), {
        "total_challenged": len(results), "survived": len(results) - len(falsified),
        "falsified": len(falsified), "falsified_ids": sorted(falsified), "reasons": reasons,
    })


# ── The whole pass over a finished review ────────────────────────────────────

def post_process_record(
    record: ReviewRecord, wt_path: str = "", changed_files: Collection[str] = (),
) -> PostProcessed:
    """Every gate and cleanup a finished review needs, as one record transform.

    The order is the contract: the ledger
    numbers the prior review, so it goes before renumbering; the drop note reads
    the finished counts, so it goes after.
    """
    verification = None
    if wt_path:
        verified = _verify_record(record, wt_path, changed_files)
        record, verification = verified.record, verified.verification
        if verification["dropped"]:
            core.log.info(f"Dropped {len(verification['dropped'])} unverified findings: "
                          f"{', '.join(verification['dropped'])}")
    record = replace(record, findings=tuple(replace(f, sid="") for f in record.findings), ledger="")
    record = renumber_record(record)
    if verification:
        record = _reconciled_record(record, verification)
    return PostProcessed(record, verification)
