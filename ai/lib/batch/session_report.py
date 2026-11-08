"""Per-PR session commands derived from findings and stored v1 decisions.

The builder shows; it never writes. Findings come from `Item.findings` plus
open v1 decisions via `batch.findings`. Commands are argv tuples.

Ordering: stack-topological on `stacked_on` (`after=[base]` while the base is
non-terminal), then severity (block > wait > act), then PR key.

Filing commands (`files_issue`) are never first, never `--track-all`, and
carry one `--track` per id. Run one only when the user asks.

Argv rules: bare `pr`; `--repo-dir` is an absolute worktree, or absolute
`repo_dir` plus `--branch` when the worktree is gone; never a relative path;
no `cd`, no `&&`, no `;`. Push `--expect` is the full 40-hex SHA.
`git -C WT` for direct git. Closeout is the bare `CLOSEOUT_COMMAND`
(`closeout owed per pr state`), never `CloseoutDebt.command` (which appends
`--track-all` when an issue is owed).

| Finding | Command |
|---|---|
| `conflict_handwritten` | `pr rebase --fix --no-push --repo-dir WT` |
| `conflict_generated` | `pr rebase --fix --no-push --repo-dir WT` |
| `stale_generated` | `git -C WT reset --hard <pre_rebase_head>` |
| `ci_failing` | `pr ci --fix --repo-dir WT` |
| `review_findings` | `pr review --self --fix --repo-dir WT` |
| `threads_need_human` | `pr comments --fix --repo-dir WT` |
| `closeout_owed` | `pr comments --finish --post --repo-dir WT` |
| `ready_to_push` | `publish.plan` push argv |
| `wrong_branch` | `git -C WT switch <branch>` |
| deferred issue | `pr comments --finish --track ID… --post --repo-dir WT` (on request) |
| open review findings | `pr review --self --track ID… --post --repo-dir WT` (on request) |
"""

# doc-group: batch

from __future__ import annotations

import shlex
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import batch.plan
import batch.publish
import review.deferred_issue
import review.finding_issue
import review.issue
import review.paths
from batch.findings import CLOSEOUT_WHY, findings_from_decisions
from batch.model import (Finding, FindingReason, Item, ItemStatus, Run, RunStatus, Severity,
                         severity_of)
from batch.publish import Refusal, TreeState
from pr.comments_fix import CLOSEOUT_COMMAND, CloseoutDebt

CloseoutFn = Callable[[str, str], CloseoutDebt]
TreeFn = Callable[[Item], TreeState | None]

COMMENT_BEFORE_PUSH = "comment on the PR before pushing: it is not a draft"
NO_CHECKOUT = "no checkout found"
PROVIDER_NOTE = "otto-workbench config set issues.provider PROVIDER --project"
TEAM_NOTE = "otto-workbench config set issues.team TEAM --project"
_ACTIVE = frozenset({RunStatus.RUNNING})
_SEV_RANK = {Severity.BLOCK: 0, Severity.WAIT: 1, Severity.ACT: 2}
_CLOSEOUT_ARGV = tuple(shlex.split(CLOSEOUT_COMMAND))


@dataclass(frozen=True)
class SessionCommand:
    argv: tuple[str, ...]
    why: str
    push: bool = False
    files_issue: bool = False

    def to_dict(self) -> dict:
        row: dict = {"argv": list(self.argv), "why": self.why}
        if self.push:
            row["push"] = True
        if self.files_issue:
            row["files_issue"] = True
        return row


@dataclass
class SessionEntry:
    pr: str
    url: str
    branch: str
    base_ref: str
    after: list[str]
    is_draft: bool
    state: str
    severity: Severity
    worktree: str
    remote_sha: str
    local_sha: str
    findings: list[Finding] = field(default_factory=list)
    commands: list[SessionCommand] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "pr": self.pr, "url": self.url, "branch": self.branch, "base_ref": self.base_ref,
            "after": list(self.after), "is_draft": self.is_draft, "state": self.state,
            "severity": self.severity.value, "worktree": self.worktree,
            "remote_sha": self.remote_sha, "local_sha": self.local_sha,
            "findings": [f.to_dict() for f in self.findings],
            "commands": [c.to_dict() for c in self.commands],
            "notes": list(self.notes), "logs": list(self.logs),
        }


def sessions(run: Run, *, closeout: CloseoutFn | None = None,
             tree: TreeFn | None = None) -> list[SessionEntry]:
    """Focused-session entries for a settled run; empty while active or waiting."""
    if run.status in _ACTIVE:
        return []
    reader = closeout or batch.plan.closeout_debt
    entries = [_session(run, item, reader, tree) for item in run.items
               if item.status is not ItemStatus.SKIPPED_CLOSED]
    return _order([e for e in entries if e.findings or e.commands])


def _session(run: Run, item: Item, closeout: CloseoutFn, tree: TreeFn | None) -> SessionEntry:
    findings = list(item.findings) + findings_from_decisions(run, item)
    debt = closeout(item.repo_dir, item.branch)
    if debt.owed:
        if not _has(findings, FindingReason.CLOSEOUT_OWED):
            findings.append(Finding(FindingReason.CLOSEOUT_OWED, "comments", CLOSEOUT_WHY))
    else:
        _drop(findings, FindingReason.CLOSEOUT_OWED)
    planned = _plan_tree(item, tree, debt)
    if planned is not None:
        _apply_plan(findings, planned)
    after = _after(run, item)
    severity = _severity(findings)
    checkout = _checkout(item)
    commands = _commands(item, findings, planned, checkout)
    notes = _notes(item, commands, checkout)
    local = planned.tree.local if planned is not None else ""
    return SessionEntry(
        pr=item.key, url=f"https://github.com/{item.repo}/pull/{item.pr}",
        branch=item.branch, base_ref=item.base_ref, after=after, is_draft=item.is_draft,
        state=item.status.value, severity=severity, worktree=item.worktree,
        remote_sha=item.remote_sha, local_sha=local, findings=findings, commands=commands,
        notes=notes, logs=[r.log_path for r in item.steps if r.log_path],
    )


@dataclass(frozen=True)
class _Planned:
    tree: TreeState
    plan: batch.publish.PublishPlan


def _plan_tree(item: Item, tree: TreeFn | None, debt: CloseoutDebt) -> _Planned | None:
    if tree is None:
        return None
    state = tree(item)
    if state is None:
        return None
    return _Planned(state, batch.publish.plan(
        item, "pr", state, closeout=CloseoutDebt(unlanded=debt.unlanded)))


def _apply_plan(findings: list[Finding], planned: _Planned) -> None:
    result = planned.plan
    if result.ok and result.pushes:
        if not _has(findings, FindingReason.READY_TO_PUSH):
            findings.append(Finding(FindingReason.READY_TO_PUSH, "publish"))
    else:
        _drop(findings, FindingReason.READY_TO_PUSH)
    if result.ok:
        return
    if result.refusal is Refusal.WRONG_BRANCH:
        if not _has(findings, FindingReason.WRONG_BRANCH):
            findings.append(Finding(FindingReason.WRONG_BRANCH, "publish", result.detail))
        return
    if not _has(findings, FindingReason.PUSH_REFUSED):
        data = {"refusal": result.refusal.value} if result.refusal is not None else {}
        findings.append(Finding(FindingReason.PUSH_REFUSED, "publish", result.detail, data=data))


def _after(run: Run, item: Item) -> list[str]:
    if not item.stacked_on:
        return []
    try:
        base = run.item(item.stacked_on)
    except KeyError:
        return []
    return [item.stacked_on] if not base.terminal else []


def _severity(findings: list[Finding]) -> Severity:
    if not findings:
        return Severity.ACT
    return min((severity_of(f) for f in findings), key=lambda s: _SEV_RANK[s])


def _has(findings: list[Finding], reason: FindingReason) -> bool:
    return any(f.reason is reason for f in findings)


def _drop(findings: list[Finding], reason: FindingReason) -> None:
    findings[:] = [f for f in findings if f.reason is not reason]


def _commands(item: Item, findings: list[Finding],
              planned: _Planned | None, checkout: str | None) -> list[SessionCommand]:
    seen: set[FindingReason] = set()
    commands: list[SessionCommand] = []
    for finding in findings:
        if finding.reason in seen:
            continue
        seen.add(finding.reason)
        if cmd := _command_for(item, finding, checkout):
            commands.append(cmd)
    if planned is not None and planned.plan.ok and planned.plan.pushes:
        commands.append(SessionCommand(
            tuple(planned.plan.commands[0]),
            "push under the planned lease",
            push=True,
        ))
    if filing := _filing_command(item, checkout):
        commands.append(filing)
    if filing := _review_filing_command(item, findings, checkout):
        commands.append(filing)
    return commands


def _command_for(item: Item, finding: Finding, checkout: str | None) -> SessionCommand | None:
    repo = _repo_args(item, checkout)
    if finding.reason is FindingReason.CONFLICT_HANDWRITTEN and repo:
        return SessionCommand(("pr", "rebase", "--fix", "--no-push", *repo),
                              "resolve hand-written conflicts")
    if finding.reason is FindingReason.CONFLICT_GENERATED and repo:
        return SessionCommand(
            ("pr", "rebase", "--fix", "--no-push", *repo),
            "rebuild generated files, or declare `rebase.regenerate` and run `pr rebase --no-push`",
        )
    if finding.reason is FindingReason.CI_FAILING and repo:
        jobs = finding.data.get("jobs") or []
        why = f"fix failing CI ({', '.join(jobs)})" if jobs else "fix failing CI"
        return SessionCommand(("pr", "ci", "--fix", *repo), why)
    if finding.reason is FindingReason.REVIEW_FINDINGS and repo:
        return SessionCommand(("pr", "review", "--self", "--fix", *repo),
                              "address open review findings")
    if finding.reason is FindingReason.STALE_GENERATED and item.pre_rebase_head:
        if git_cmd := _git(item, checkout, "reset", "--hard", item.pre_rebase_head):
            return SessionCommand(git_cmd, "undo the rebase, then regenerate")
    if finding.reason is FindingReason.THREADS_NEED_HUMAN and repo:
        return SessionCommand(("pr", "comments", "--fix", *repo), "address review comments")
    if finding.reason is FindingReason.CLOSEOUT_OWED and repo:
        return SessionCommand((*_CLOSEOUT_ARGV, *repo), CLOSEOUT_WHY)
    if finding.reason is FindingReason.WRONG_BRANCH:
        if git_cmd := _git(item, checkout, "switch", item.branch):
            return SessionCommand(git_cmd, f"check out {item.branch}")
    return None


def _review_filing_command(item: Item, findings: list[Finding],
                           checkout: str | None) -> SessionCommand | None:
    if not any(f.reason is FindingReason.REVIEW_FINDINGS for f in findings):
        return None
    if checkout is None or checkout != item.worktree:
        return None
    ids = [f.id for f in review.finding_issue.filable(
        review.paths.self_review_dir(item.repo, item.branch))]
    if not ids:
        return None
    argv: list[str] = ["pr", "review", "--self"]
    for finding_id in ids:
        argv.extend(["--track", finding_id])
    argv.extend(["--post", "--repo-dir", checkout])
    return SessionCommand(
        tuple(argv),
        "file tracking issues for open review findings (only on request; runs no review)",
        files_issue=True,
    )


def _filing_command(item: Item, checkout: str | None) -> SessionCommand | None:
    repo = _repo_args(item, checkout)
    if repo is None or checkout is None or not Path(checkout).is_dir():
        return None
    state = batch.plan.pr_state(item.repo_dir, item.branch)
    if state is None:
        return None
    ids = [o.id for o in review.deferred_issue.deferred_outcomes(state) if o.id]
    pending = state.fix.deferred_issue_pending
    if not pending and not (ids and not state.fix.deferred_issue_id):
        return None
    argv: list[str] = ["pr", "comments", "--finish"]
    for outcome_id in ids:
        argv.extend(["--track", outcome_id])
    argv.extend(["--post", *repo])
    return SessionCommand(
        tuple(argv),
        "file a tracking issue for deferred threads (only on request)",
        files_issue=True,
    )


def _notes(item: Item, commands: list[SessionCommand], checkout: str | None) -> list[str]:
    notes: list[str] = []
    if any(c.push for c in commands) and not item.is_draft:
        notes.append(COMMENT_BEFORE_PUSH)
    if checkout is None:
        notes.append(NO_CHECKOUT)
    if not any(c.files_issue for c in commands):
        return notes
    info = review.issue.load_issue_provider(checkout, adopt=False)
    if not info.resolved:
        notes.append(PROVIDER_NOTE)
        if review.issue.needs_team_key(info.name):
            notes.append(TEAM_NOTE)
    return notes


def _checkout(item: Item) -> str | None:
    wt = item.worktree
    if wt and Path(wt).is_dir():
        return wt if Path(wt).is_absolute() else None
    repo = item.repo_dir
    if repo and Path(repo).is_absolute():
        return repo
    return None


def _repo_args(item: Item, checkout: str | None) -> tuple[str, ...] | None:
    if checkout is None:
        return None
    if checkout == item.worktree:
        return ("--repo-dir", checkout)
    return ("--repo-dir", checkout, "--branch", item.branch)


def _git(item: Item, checkout: str | None, *args: str) -> tuple[str, ...] | None:
    if checkout is None or checkout != item.worktree:
        return None
    return ("git", "-C", checkout, *args)


def _order(entries: list[SessionEntry]) -> list[SessionEntry]:
    by_key = {e.pr: e for e in entries}
    remaining = set(by_key)
    ordered: list[SessionEntry] = []
    while remaining:
        ready = [k for k in remaining if not any(a in remaining for a in by_key[k].after)]
        if not ready:
            ready = sorted(remaining)
        ready.sort(key=lambda k: (_SEV_RANK[by_key[k].severity], k))
        pick = ready[0]
        ordered.append(by_key[pick])
        remaining.remove(pick)
    return ordered
