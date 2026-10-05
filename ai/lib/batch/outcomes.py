"""Turn what a finished step left behind into a step status and decisions."""

# doc-group: batch

from __future__ import annotations

import json
import re
from dataclasses import dataclass

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

# The phrase core.run_lock.report_busy prints; confirmed in Task 0 Step 5.
LOCK_BUSY_MARKER = "another pr run already owns this"
_OWED = (pr.fix.FixOutcome.NEEDS_HUMAN, pr.fix.FixOutcome.DEFERRED)
_SYNTHETIC_ID = re.compile(r"^(ic|rb)-\d+-\d+$")


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
        sha, _, values = line.partition("\t")
        for value in (v.strip() for v in values.split(",")):
            if value in pr.fix.UNVERIFIED_CHECKS:
                found.append({"commit": sha, "status": value})
    return found


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


def _failed(exit_code: int, log_tail: list[str]) -> StepResult:
    # The lock-busy message is the child's final stderr line and log_tail keeps
    # the last 40 lines, so it is always inside the tail.
    reason = "busy" if any(LOCK_BUSY_MARKER in line for line in log_tail) else "error"
    return StepResult(StepStatus.FAILED, [DecisionDraft(DecisionKind.FAILED, {
        "reason": reason, "exit_code": exit_code, "log_tail": log_tail})])


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
             log_tail: list[str], head_before: str = "", watch: bool = False) -> StepResult:
    """The status and decisions a finished step leaves, read from its stdout and the tree.

    A clean exit yields at most one `step_review` decision, carrying every
    piece of evidence found, so two decisions on one step cannot disagree.
    *head_before* is the HEAD the step's first attempt started from; the
    `Fix-Checks:` trailers are read from every commit after it. A *watch* run
    is read before its exit code, which says nothing a final report does not.
    """
    if watch:
        return _watched(stdout)
    if step is Step.REBASE and exit_code == CONFLICTS_EXIT:
        return _needs(DecisionKind.REBASE_CONFLICT, [_rebase_payload(stdout, log_tail)])
    if step is Step.REBASE and exit_code == REFUSAL_EXIT:
        return _needs(DecisionKind.REBASE_REFUSED, [_rebase_payload(stdout, log_tail)])
    if exit_code != 0:
        return _failed(exit_code, log_tail)
    drafts: list[DecisionDraft] = []
    if step is Step.COMMENTS:
        drafts.extend(DecisionDraft(DecisionKind.COMMENT_ITEM, p) for p in comment_items(item))
    if evidence := _evidence(step, stdout, item, head_before):
        drafts.append(DecisionDraft(DecisionKind.STEP_REVIEW, {"evidence": evidence}))
    raw = (_json(stdout) or {}).get("pre_rebase_head", "") if step is Step.REBASE else ""
    pre = raw if isinstance(raw, str) else ""
    status = StepStatus.NEEDS_DECISION if drafts else StepStatus.DONE
    return StepResult(status, drafts, pre_rebase_head=pre)
