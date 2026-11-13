"""Turn what a finished step left behind into a step status and decisions.

`classify(..., fixing=True)` treats CI evidence as `ci_unfixed`. A read-only
CI step passes `fixing=False` and uses `ci_failing`.

A `failed` finding's `reason` is one of these, or a publish refusal from
`batch.publish.Refusal`:

- `ai_prompt_failed`: the step's AI prompt failed
- `review_orchestration_failed`: the review produced no review file
- `commit_failed`: a fix commit was refused by a hook
- `pre_push_rejected`: the pre-push hook refused, or uncommitted changes blocked the push
- `unanswered_handbacks`: `--finish --post` refused the summary because a thread handed to a
  person has no reply or settlement on GitHub; answer it with `pr comments`, then retry
- `lock_busy`: another `pr` run already owns the target
- `push_rejected`: the remote refused the push
- `worktree_collision`: the PR's worktree path holds a worktree on another branch
- `quota_exhausted`: the AI provider turned the step away on quota or a spend limit
- `owners_unconfigured`: `github.owners` is not configured, so every GitHub write was refused
- `mise_untrusted`: a hook could not run because a mise config in the worktree is not trusted
- `github`: GitHub could not be read
- `error`: anything the batch does not recognise
"""

# doc-group: batch

from __future__ import annotations

import json
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import batch.publish
import core.proc
import core.quota_throttle
import gh.owners
import git.client
import git.regenerate
import pr.ci_report
import pr.fix
import pr.settlement
import pr.state
import pr.target
import review.document
import review.paths
from batch.model import DecisionKind, EvidenceKind, Item, Step, StepStatus
from rebase.types import CONFLICTS_EXIT, REFUSAL_EXIT
from review.types import severity_by_key

# The phrase core.run_lock.LockBusy prints (core/run_lock.py).
LOCK_BUSY_MARKER = "another pr run already owns this"
# Emitted at git/land.py.
COMMIT_FAILED_MARKER = "commit failed:"
# Claude Code 2.1.292's limit-prefix list (You've hit your / You're out of …);
# no module of ours emits these — they reach a step's stderr only through
# agent.backend_claude._failure_detail → core.log.dim, or invoke_fix's echo.
_PROVIDER_LIMIT_PHRASES = (
    "you've hit your", "you're out of usage credits", "you're out of extra usage",
    "your org is out of usage", "usage limit reached",
)
# The steps that run a fix engine: they commit, and they leave Fix-Checks trailers.
_FIXING_STEPS = (Step.REVIEW, Step.CI, Step.COMMENTS)
_OWED = (pr.fix.FixOutcome.NEEDS_HUMAN, pr.fix.FixOutcome.DEFERRED)
_SYNTHETIC_ID = re.compile(r"^(ic|rb)-\d+-\d+$")

# How many trailing log lines a failure keeps: the scheduler's live tail and a
# failed publish's payload both cut to this.
LOG_TAIL_LINES = 40
_ANSI = re.compile(r"\x1b\[[0-9;]*m")
# The glyphs core.log leads a line with.
_GLYPHS = "✗⚠▸✓● "
_SESSION_LOG = re.compile(r"Session log:\s*(\S+)")


class FailureReason(StrEnum):
    """Why a step or a publish failed, read from what it printed. Persisted in payloads."""

    AI_PROMPT_FAILED = "ai_prompt_failed"
    REVIEW_ORCHESTRATION_FAILED = "review_orchestration_failed"
    COMMIT_FAILED = "commit_failed"
    PRE_PUSH_REJECTED = "pre_push_rejected"
    LOCK_BUSY = "lock_busy"
    UNANSWERED_HANDBACKS = "unanswered_handbacks"
    PUSH_REJECTED = "push_rejected"
    WORKTREE_COLLISION = "worktree_collision"
    QUOTA_EXHAUSTED = "quota_exhausted"
    OWNERS_UNCONFIGURED = "owners_unconfigured"
    MISE_UNTRUSTED = "mise_untrusted"
    GITHUB = "github"
    ERROR = "error"


@dataclass(frozen=True)
class Failure:
    """A classified failure: the reason, the line that showed it, and any session log named."""

    reason: FailureReason
    detail: str = ""
    session_log: str = ""


@dataclass(frozen=True)
class _Marker:
    reason: FailureReason
    phrases: tuple[str, ...] = ()
    # Emitter text owned as a regex elsewhere, matched against the line as printed.
    pattern: re.Pattern[str] | None = None

    def hits(self, line: str) -> bool:
        lowered = line.lower()
        return any(p in lowered for p in self.phrases) or bool(
            self.pattern and self.pattern.search(line))


# Most specific outcome first, and the first marker with any matching line
# wins: a hook rejection also prints "push refused", and a push after a review
# mentions the review. Phrases are lowercase; lines are compared lowercased.
# Text matching no marker is `error` — never a guess at a nearer reason.
_MARKERS = (
    _Marker(FailureReason.MISE_UNTRUSTED, pattern=git.regenerate.MISE_UNTRUSTED),
    _Marker(FailureReason.COMMIT_FAILED, (COMMIT_FAILED_MARKER,)),
    _Marker(FailureReason.LOCK_BUSY, (LOCK_BUSY_MARKER,)),
    _Marker(FailureReason.OWNERS_UNCONFIGURED, (gh.owners.UNCONFIGURED.lower(),)),
    _Marker(FailureReason.UNANSWERED_HANDBACKS, (pr.settlement.UNANSWERED_HANDBACKS_MARKER.lower(),)),
    _Marker(FailureReason.PRE_PUSH_REJECTED, (
        "push refused (hook)", "pre-push checks failed",
        "left uncommitted changes — not pushing")),
    _Marker(FailureReason.PUSH_REJECTED, (
        "push refused", "push dropped", "remote did not move",
        "remote does not hold the commit")),
    _Marker(FailureReason.REVIEW_ORCHESTRATION_FAILED, (
        "produced no review file", "review orchestration failed")),
    _Marker(FailureReason.QUOTA_EXHAUSTED, (
        core.quota_throttle.QUOTA_EXHAUSTED_PHRASE.lower(), *_PROVIDER_LIMIT_PHRASES)),
    _Marker(FailureReason.AI_PROMPT_FAILED, ("ai prompt failed",)),
)


def _clean(line: str) -> str:
    return _ANSI.sub("", line).strip().lstrip(_GLYPHS).strip()


def classify_failure(lines: Sequence[str]) -> Failure:
    """Why a failed step or publish failed, from the lines it printed.

    The detail is the first line that matched, with colour codes and core.log's
    glyph stripped; unrecognised text is `error` with no detail. A `Session log:`
    line names the review session the failure left, if any.
    """
    cleaned = [_clean(line) for line in lines]
    session = next((m.group(1) for line in reversed(cleaned)
                    if (m := _SESSION_LOG.search(line))), "")
    for marker in _MARKERS:
        hit = next((line for line in cleaned if marker.hits(line)), "")
        if hit:
            return Failure(marker.reason, hit, session)
    return Failure(FailureReason.ERROR, "", session)


@dataclass(frozen=True)
class DecisionDraft:
    kind: DecisionKind
    payload: dict


@dataclass(frozen=True)
class StepResult:
    status: StepStatus
    decisions: list[DecisionDraft]
    # The tip a completed rebase started from, from its stdout report.
    pre_rebase_head: str = ""


def _json(stdout: str) -> dict | None:
    try:
        value = json.loads(stdout)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def stream_reports(stdout: str) -> list[dict]:
    """Every JSON object a child printed, whether alone or as `---`-led stream documents.

    Splits only on lines that are exactly `---`, so a report carrying a diff
    header such as `--- a/file` stays one document.
    """
    docs = []
    for chunk in re.split(r"^---$", stdout, flags=re.M):
        value = _json(chunk.strip())
        if value is not None:
            docs.append(value)
    return docs


def last_report(stdout: str, report_type: str) -> dict | None:
    """The last stream document of *report_type*, or None."""
    typed = [d for d in stream_reports(stdout) if d.get("type") == report_type]
    return typed[-1] if typed else None


def ci_failing(stdout: str) -> dict | None:
    """Failing jobs from a read-only ci-check report, or None when there are none.

    Uses the last stream document that carries `failures` (untyped or
    `type: final`). A green read-only check prints nothing.
    """
    docs = [d for d in stream_reports(stdout) if d.get("failures") is not None
            and ("type" not in d or d.get("type") == "final")]
    if not docs:
        return None
    failures = docs[-1]["failures"]
    if not failures:
        return None
    jobs = sorted({f["job"] for f in failures if f.get("job")})
    return {"jobs": jobs, "failures": len(failures)}


def ci_unfixed(stdout: str) -> dict | None:
    """The CI failures this run left standing, from ci-check's own tally.

    A run that printed no tally is not taken as clean: every successful
    `--fix` prints one, so its absence is something a person should look at.
    """
    tally = last_report(stdout, pr.ci_report.FIX_TALLY_TYPE)
    if tally is None:
        return {"detail": "ci-check printed no fix tally"}
    if tally.get("unfixed") or tally.get("skipped"):
        return {"unfixed": tally.get("unfixed", []), "skipped": tally.get("skipped", [])}
    return None


def fix_checks(worktree: str, head_before: str) -> list[dict]:
    """Unverified `Fix-Checks:` trailers on the commits in head_before..HEAD.

    Each entry is `{"commit", "status"}`. Read off the commits the step made,
    so a verdict some earlier run left behind can never be mistaken for this
    one. A commit with no trailer — one an agent made outside the fix engine —
    is no evidence rather than an error. A range git cannot read is one entry
    with status `unreadable`, so the step is held for a person rather than
    taken as clean.
    """
    # With no lower bound there is no range of the step's own commits to read:
    # every commit reachable from HEAD would be scanned, and a verdict an
    # earlier run left behind would be taken for this step's.
    if not (worktree and head_before):
        return []
    fmt = f"--format=%H%x09%(trailers:key={pr.fix.CHECKS_TRAILER},valueonly,separator=%x2C)"
    r = git.client.run("log", fmt, f"{head_before}..HEAD", cwd=worktree)
    # Fail closed: a failed or timed-out read says nothing about the verdicts,
    # and an empty list would let a `Fix-Checks: red` commit classify DONE.
    if not r.ok:
        return [{"commit": "", "status": "unreadable"}]
    found = []
    for line in r.stdout.splitlines():
        found.extend(_unverified_from_line(line))
    return found


def _unverified_from_line(line: str) -> list[dict]:
    sha, _, values = line.partition("\t")
    return [{"commit": sha, "status": value}
            for value in (v.strip() for v in values.split(","))
            if value in pr.fix.UNVERIFIED_CHECKS]


def _evidence(step: Step, stdout: str, item: Item, head_before: str,
              *, fixing: bool = True) -> list[dict]:
    found: list[dict] = []
    if step is Step.REVIEW and (findings := open_findings(item)):
        found.append({"kind": EvidenceKind.OPEN_FINDINGS.value, "findings": findings})
    if step is Step.REBASE:
        report = _json(stdout) or {}
        if report.get("files_one_sided"):
            found.append({"kind": EvidenceKind.ONE_SIDED.value,
                          "files": report["files_one_sided"],
                          "regions": report.get("one_sided_regions", [])})
        if report.get("files_stale"):
            found.append({"kind": EvidenceKind.STALE.value, "files": report["files_stale"]})
    if step is Step.CI:
        extra = ci_unfixed(stdout) if fixing else ci_failing(stdout)
        kind = EvidenceKind.CI_UNFIXED if fixing else EvidenceKind.CI_FAILING
        if extra:
            found.append({"kind": kind.value, **extra})
    if step in _FIXING_STEPS:
        found.extend({"kind": EvidenceKind.CHECKS_UNVERIFIED.value, **check}
                     for check in fix_checks(item.worktree, head_before))
    return found


def _rebase_payload(stdout: str, log_tail: list[str]) -> dict:
    parsed = _json(stdout)
    return parsed if parsed is not None else {"log_tail": log_tail}


def _load_pr_state(item: Item):
    key = pr.target.repo_key_from_origin(item.worktree or item.repo_dir)
    return pr.state.load_state(pr.target.target_dir(key, item.branch)) if key else None


def comment_items(item: Item) -> list[dict]:
    state = _load_pr_state(item)
    if state is None:
        return []
    return [{"id": i.id, "outcome": i.outcome.value, "summary": i.summary, "reason": i.reason,
             "file": i.file, "line": i.line, "replyable": not _SYNTHETIC_ID.match(i.id)}
            for i in state.fix.fix.items if i.outcome in _OWED]


def open_findings(item: Item) -> list[dict]:
    path = review.paths.self_review_file_path(item.repo, item.branch)
    doc = review.document.ReviewDocument.read(path)
    if doc is None:
        return []
    # Finding stores the description in `body`, not `title`. The payload key stays `title`.
    return [{"severity": severity_by_key(f.severity).label, "title": f.body,
             "declined": f.declined} for f in doc.open_findings]


def _failed(exit_code: int, log_tail: list[str], log_path: str) -> StepResult:
    # The lock-busy message is the child's final stderr line and log_tail keeps
    # the last LOG_TAIL_LINES lines, so it is always inside the tail.
    found = classify_failure(log_tail)
    payload: dict = {"reason": found.reason.value, "exit_code": exit_code, "log_tail": log_tail}
    if found.detail:
        payload["detail"] = found.detail
    if log_path:
        payload["log"] = log_path
    if found.session_log:
        payload["session_log"] = found.session_log
    return StepResult(StepStatus.FAILED, [DecisionDraft(DecisionKind.FAILED, payload)])


def _needs(kind: DecisionKind, payloads: list[dict]) -> StepResult:
    return StepResult(StepStatus.NEEDS_DECISION, [DecisionDraft(kind, p) for p in payloads])


def _commit_refused(lines: Sequence[str]) -> bool:
    return any(COMMIT_FAILED_MARKER in _clean(l).lower() for l in lines)


TICKET_TIMEOUT_MARKER = "found no room in"


def _wait_ended(exit_code: int, log_tail: Sequence[str], *, cancelled: bool) -> bool:
    """Whether *exit_code* is a requested stop or a ticket timeout, not a step failure.

    A waiting `pr` exits 128+N on SIGINT/SIGTERM/SIGHUP; that is an interrupt
    only when the run is being cancelled, else it is a failure that must show.
    75 is an interrupt only when it carries the ticket timeout text;
    any other 75 (queue busy, lock busy, quota) stays a failure.
    """
    if exit_code - 128 in core.proc.STOP_SIGNALS:
        return cancelled
    return (exit_code == os.EX_TEMPFAIL
            and any(TICKET_TIMEOUT_MARKER in _clean(l) for l in log_tail))


def classify(step: Step, exit_code: int, stdout: str, *, item: Item,
             log_tail: list[str], head_before: str = "", log_path: str = "",
             fixing: bool = True, cancelled: bool = False) -> StepResult:
    """The status and decisions a finished step leaves, read from its stdout and the tree.

    A clean exit yields at most one `step_review` decision, carrying every
    piece of evidence found, so two decisions on one step cannot disagree.
    *head_before* is the HEAD the step's first attempt started from; the
    `Fix-Checks:` trailers are read from every commit after it.
    *log_path* is the step's log, recorded on a failed decision.
    A stop-signal exit while *cancelled*, or a ticket timeout (75 with the
    ticket timeout text), is interrupted, not a failed decision with a fix suggestion.
    """
    if step is Step.REBASE and exit_code == CONFLICTS_EXIT:
        return _needs(DecisionKind.REBASE_CONFLICT, [_rebase_payload(stdout, log_tail)])
    if step is Step.REBASE and exit_code == REFUSAL_EXIT:
        return _needs(DecisionKind.REBASE_REFUSED, [_rebase_payload(stdout, log_tail)])
    if _wait_ended(exit_code, log_tail, cancelled=cancelled):
        return StepResult(StepStatus.INTERRUPTED, [])
    if exit_code != 0:
        return _failed(exit_code, log_tail, log_path)
    if step in _FIXING_STEPS and _commit_refused(log_tail):
        return _failed(exit_code, log_tail, log_path)
    drafts: list[DecisionDraft] = []
    if step is Step.COMMENTS:
        drafts.extend(DecisionDraft(DecisionKind.COMMENT_ITEM, p) for p in comment_items(item))
    if evidence := _evidence(step, stdout, item, head_before, fixing=fixing):
        drafts.append(DecisionDraft(DecisionKind.STEP_REVIEW, {"evidence": evidence}))
    raw = (_json(stdout) or {}).get("pre_rebase_head", "") if step is Step.REBASE else ""
    pre = raw if isinstance(raw, str) else ""
    status = StepStatus.NEEDS_DECISION if drafts else StepStatus.DONE
    return StepResult(status, drafts, pre_rebase_head=pre)
