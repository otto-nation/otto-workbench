"""Turn what a finished step left behind into a step status and decisions.

A `failed` decision's `reason` is a `FailureReason` or a publish refusal value.
Each maps to one sentence, which status prints as `why`.
"""

# doc-group: batch

from __future__ import annotations

import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum

import batch.publish
import git.client
import pr.ci_report
import pr.fix
import pr.state
import pr.target
import review.document
import review.paths
from batch.model import DecisionKind, EvidenceKind, Item, Step, StepStatus
from rebase.types import CONFLICTS_EXIT, REFUSAL_EXIT
from review.types import severity_by_key

# The phrase core.run_lock.LockBusy prints (core/run_lock.py).
LOCK_BUSY_MARKER = "another pr run already owns this"
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
    PRE_PUSH_REJECTED = "pre_push_rejected"
    LOCK_BUSY = "lock_busy"
    PUSH_REJECTED = "push_rejected"
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
    phrases: tuple[str, ...]


# Most specific outcome first, and the first marker with any matching line
# wins: a hook rejection also prints "push refused", and a push after a review
# mentions the review. Phrases are lowercase; lines are compared lowercased.
# Text matching no marker is `error` — never a guess at a nearer reason.
_MARKERS = (
    _Marker(FailureReason.LOCK_BUSY, (LOCK_BUSY_MARKER,)),
    _Marker(FailureReason.PRE_PUSH_REJECTED, (
        "push refused (hook)", "pre-push checks failed",
        "left uncommitted changes — not pushing")),
    _Marker(FailureReason.PUSH_REJECTED, (
        "push refused", "push dropped", "remote did not move",
        "remote does not hold the commit")),
    _Marker(FailureReason.REVIEW_ORCHESTRATION_FAILED, (
        "produced no review file", "review orchestration failed")),
    _Marker(FailureReason.AI_PROMPT_FAILED, ("ai prompt failed",)),
)

FAILURE_WHY: dict[str, str] = {
    FailureReason.AI_PROMPT_FAILED.value: "an AI prompt the step depends on failed",
    FailureReason.REVIEW_ORCHESTRATION_FAILED.value:
        "the self-review's agents exited before writing a review",
    FailureReason.PRE_PUSH_REJECTED.value:
        "the pre-push checks rejected the push (a failing hook, or uncommitted work)",
    FailureReason.LOCK_BUSY.value: "another pr run held this branch's lock",
    FailureReason.PUSH_REJECTED.value: "git refused the push, or the remote did not keep it",
    FailureReason.GITHUB.value: "GitHub could not be asked about the PR",
    FailureReason.ERROR.value: "the step failed for a reason the batch does not recognise",
    batch.publish.Refusal.REMOTE_MOVED.value: "someone pushed to the PR branch since the batch planned it",
    batch.publish.Refusal.NOT_COMPARABLE.value: "the local and remote branches could not be compared",
    batch.publish.Refusal.NOT_INCORPORATED.value:
        "the rebase started from a tip that does not contain the PR's remote head",
    batch.publish.Refusal.NOT_INCORPORATED_REMOTE.value:
        "the remote has commits a push of the local branch would drop",
    batch.publish.Refusal.FETCH_FAILED.value: "the PR branch could not be fetched",
}
# Written by runs before `lock_busy` existed; state files still carry it.
FAILURE_WHY["busy"] = FAILURE_WHY[FailureReason.LOCK_BUSY]


def why(reason: str) -> str:
    """The sentence for a failed decision's `reason`; unknown reasons read as `error`."""
    return FAILURE_WHY.get(reason, FAILURE_WHY[FailureReason.ERROR.value])


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
        hit = next((line for line in cleaned
                    if any(p in line.lower() for p in marker.phrases)), "")
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


def _evidence(step: Step, stdout: str, item: Item, head_before: str) -> list[dict]:
    found: list[dict] = []
    if step is Step.REVIEW and (findings := open_findings(item)):
        found.append({"kind": EvidenceKind.OPEN_FINDINGS.value, "findings": findings})
    if step is Step.REBASE:
        report = _json(stdout) or {}
        if report.get("files_one_sided"):
            found.append({"kind": EvidenceKind.ONE_SIDED.value,
                          "files": report["files_one_sided"],
                          "regions": report.get("one_sided_regions", [])})
    if step is Step.CI and (unfixed := ci_unfixed(stdout)):
        found.append({"kind": EvidenceKind.CI_UNFIXED.value, **unfixed})
    if step in (Step.REVIEW, Step.CI, Step.COMMENTS):
        found.extend({"kind": EvidenceKind.CHECKS_UNVERIFIED.value, **check}
                     for check in fix_checks(item.worktree, head_before))
    return found


def _rebase_payload(stdout: str, log_tail: list[str]) -> dict:
    parsed = _json(stdout)
    return parsed if parsed is not None else {"log_tail": log_tail}


def _load_pr_state(item: Item):
    key = pr.target.repo_key_from_origin(item.worktree or item.repo_dir)
    return pr.state.load_state(pr.target.target_dir(key, item.branch)) if key else None


def recorded_pre_rebase_head(item: Item) -> str:
    """The pre-rebase tip `pr rebase` saved for *item*'s branch, or ""."""
    state = _load_pr_state(item)
    return state.rebase.pre_rebase_head if state is not None else ""


def comment_items(item: Item) -> list[dict]:
    state = _load_pr_state(item)
    if state is None:
        return []
    return [{"id": i.id, "outcome": i.outcome.value, "summary": i.summary, "reason": i.reason,
             "file": i.file, "line": i.line, "replyable": not _SYNTHETIC_ID.match(i.id)}
            for i in state.fix.fix.items if i.outcome in _OWED]


def open_findings(item: Item) -> list[dict]:
    path = review.paths.self_review_file_path(item.repo, item.branch)
    if not path.is_file():
        return []
    doc = review.document.ReviewDocument.parse(path.read_text())
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


def settled_report(stdout: str) -> dict | None:
    """The final report of a `--wait` run whose CI completed, or None.

    A wait that timed out still prints a final report, with whatever failures
    had arrived by then, so a report that is not `completed` read nothing whole.
    """
    final = last_report(stdout, pr.ci_report.FINAL_REPORT_TYPE)
    return final if final is not None and final.get("status") == "completed" else None


def _watched(stdout: str) -> StepResult:
    """A post-publish re-check: green finishes the step, red re-runs it as a fix.

    No settled report — no checks found yet (`ci-check --wait` exits non-zero
    seconds after a push), or a wait that timed out — is not a failure to
    decide on: the step finishes and the run summary says CI was not re-checked.
    """
    final = settled_report(stdout)
    red = final is not None and bool(final.get("failures"))
    return StepResult(StepStatus.PENDING if red else StepStatus.DONE, [])


def _needs(kind: DecisionKind, payloads: list[dict]) -> StepResult:
    return StepResult(StepStatus.NEEDS_DECISION, [DecisionDraft(kind, p) for p in payloads])


def classify(step: Step, exit_code: int, stdout: str, *, item: Item,
             log_tail: list[str], head_before: str = "", watch: bool = False,
             log_path: str = "") -> StepResult:
    """The status and decisions a finished step leaves, read from its stdout and the tree.

    A clean exit yields at most one `step_review` decision, carrying every
    piece of evidence found, so two decisions on one step cannot disagree.
    *head_before* is the HEAD the step's first attempt started from; the
    `Fix-Checks:` trailers are read from every commit after it. A *watch* run
    is read before its exit code, which says nothing a final report does not.
    *log_path* is the step's log, recorded on a failed decision.
    """
    if watch:
        return _watched(stdout)
    if step is Step.REBASE and exit_code == CONFLICTS_EXIT:
        return _needs(DecisionKind.REBASE_CONFLICT, [_rebase_payload(stdout, log_tail)])
    if step is Step.REBASE and exit_code == REFUSAL_EXIT:
        return _needs(DecisionKind.REBASE_REFUSED, [_rebase_payload(stdout, log_tail)])
    if exit_code != 0:
        return _failed(exit_code, log_tail, log_path)
    drafts: list[DecisionDraft] = []
    if step is Step.COMMENTS:
        drafts.extend(DecisionDraft(DecisionKind.COMMENT_ITEM, p) for p in comment_items(item))
    if evidence := _evidence(step, stdout, item, head_before):
        drafts.append(DecisionDraft(DecisionKind.STEP_REVIEW, {"evidence": evidence}))
    raw = (_json(stdout) or {}).get("pre_rebase_head", "") if step is Step.REBASE else ""
    pre = raw if isinstance(raw, str) else ""
    status = StepStatus.NEEDS_DECISION if drafts else StepStatus.DONE
    return StepResult(status, drafts, pre_rebase_head=pre)
