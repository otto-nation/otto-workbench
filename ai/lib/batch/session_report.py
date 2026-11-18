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

| Finding | Command | Action |
|---|---|---|
| `conflict_handwritten` | `pr rebase --fix --no-push --repo-dir WT` | `rebase` |
| `conflict_generated` | `pr rebase --fix --no-push --repo-dir WT` | `rebase_generated` |
| `stale_generated` | guidance note; the lease push stays in `commands` | |
| `ci_failing` | `pr ci --fix --repo-dir WT` | `ci` |
| `review_findings` | `pr review --self --fix --repo-dir WT` | `review` |
| `threads_need_human` | `pr comments --fix --repo-dir WT` | `comments` |
| `closeout_owed` | `pr comments --finish --post --repo-dir WT` | `closeout` |
| `ready_to_push` | `publish.plan` push argv | `push` |
| `push_refused` (`not_incorporated_remote`) | `pr rebase --push-only --expect SHA --repo-dir WT` | `push` |
| `wrong_branch` | `git -C WT switch <branch>` | `switch_branch` |
| deferred issue | `pr comments --finish --track ID… --post --repo-dir WT` (on request) | `file_threads_issue` |
| open review findings | `pr review --self --track ID… --post --repo-dir WT` (on request) | `file_review_issue` |
"""

# doc-group: batch

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import batch.plan
import batch.publish
import git.client
import pr.needs_person
import review.deferred_issue
import review.finding_issue
import review.issue
import review.paths
from batch.findings import findings_from_decisions
from batch.model import (Finding, FindingReason, Item, ItemStatus, Run, RunStatus, Severity,
                         severity_of)
from batch.publish import Refusal, TreeState
from pr.comments_fix import CloseoutDebt
from pr.needs_person import CLOSEOUT_WHY

CloseoutFn = Callable[[str, str], CloseoutDebt]
TreeFn = Callable[[Item], TreeState | None]

COMMENT_BEFORE_PUSH = "comment on the PR before pushing: it is not a draft"
NO_CHECKOUT = "no checkout found"
PROVIDER_NOTE = "otto-workbench config set issues.provider PROVIDER --project"
TEAM_NOTE = "otto-workbench config set issues.team TEAM --project"

# The findings the report derives at read time rather than reading from the
# run's state, each with the step it is shown under. One table, so the report
# and `pr batch note` agree on which findings a session can show.
DERIVED_STEPS: dict[FindingReason, str] = {
    FindingReason.CLOSEOUT_OWED: pr.needs_person.FINDING_STEPS[pr.needs_person.CLOSEOUT_OWED],
    FindingReason.READY_TO_PUSH: "publish",
    FindingReason.WRONG_BRANCH: "publish",
    FindingReason.PUSH_REFUSED: "publish",
}


def finding_key(finding: Finding) -> str:
    """How a session and `pr batch note` name one finding: `<reason>:<step>`."""
    return f"{finding.reason.value}:{finding.step}"


def finding_keys(run: Run, item: Item) -> frozenset[str]:
    """Every finding key a session for *item* can show: stored, mapped from v1, or derived."""
    stored = list(item.findings) + findings_from_decisions(run, item)
    derived = {f"{reason.value}:{step}" for reason, step in DERIVED_STEPS.items()}
    return frozenset(finding_key(f) for f in stored) | derived


def _derived(reason: FindingReason, detail: str = "", data: dict | None = None) -> Finding:
    return Finding(reason, DERIVED_STEPS[reason], detail, data=dict(data or {}))


_ACTIVE = frozenset({RunStatus.RUNNING})
_SEV_RANK = {Severity.BLOCK: 0, Severity.WAIT: 1, Severity.ACT: 2}


@dataclass(frozen=True)
class SessionCommand:
    argv: tuple[str, ...]
    why: str
    action: str
    push: bool = False
    files_issue: bool = False

    def to_dict(self) -> dict:
        row: dict = {"argv": list(self.argv), "why": self.why, "action": self.action}
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
    """Focused-session entries for settled items; in-flight items are skipped while running."""
    items = run.items
    if run.status in _ACTIVE:
        items = [item for item in items if item.terminal]
    reader = closeout or batch.plan.closeout_debt
    wanted = [item for item in items if item.status is not ItemStatus.SKIPPED_CLOSED]
    if not wanted:
        return []
    with ThreadPoolExecutor(max_workers=min(8, len(wanted))) as pool:
        # Submit in input order and collect in that same order; do not use
        # as_completed, which would scramble the list _order then reads.
        futures = [pool.submit(_session, run, item, reader, tree) for item in wanted]
        entries = [future.result() for future in futures]
    return _order([e for e in entries if e.findings or e.commands])


def _session(run: Run, item: Item, closeout: CloseoutFn, tree: TreeFn | None) -> SessionEntry:
    findings = list(item.findings) + findings_from_decisions(run, item)
    debt = closeout(item.repo_dir, item.branch)
    if debt.owed:
        if not _has(findings, FindingReason.CLOSEOUT_OWED):
            findings.append(_derived(FindingReason.CLOSEOUT_OWED, CLOSEOUT_WHY))
    else:
        _drop(findings, FindingReason.CLOSEOUT_OWED)
    planned = _plan_tree(item, tree, debt, no_verify=run.no_verify)
    _refresh_stored(item, findings, planned)
    if planned is not None:
        _apply_plan(findings, planned)
    after = _after(run, item)
    severity = severity_for(findings)
    checkout = _checkout(item)
    commands = _commands(run, item, findings, planned, checkout)
    notes = _notes(item, findings, commands, checkout)
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


def _plan_tree(item: Item, tree: TreeFn | None, debt: CloseoutDebt, *,
               no_verify: bool = False) -> _Planned | None:
    if tree is None:
        return None
    state = tree(item)
    if state is None:
        return None
    return _Planned(state, batch.publish.plan(
        item, "pr", state, closeout=CloseoutDebt(unlanded=debt.unlanded),
        no_verify=no_verify))


def _apply_plan(findings: list[Finding], planned: _Planned) -> None:
    result = planned.plan
    stored_refusal = _has(findings, FindingReason.PUSH_REFUSED) or _has(
        findings, FindingReason.WRONG_BRANCH)
    if result.ok and result.pushes and stored_refusal:
        return
    if result.ok and result.pushes:
        if not _has(findings, FindingReason.READY_TO_PUSH):
            findings.append(_derived(FindingReason.READY_TO_PUSH))
    else:
        _drop(findings, FindingReason.READY_TO_PUSH)
    if result.ok:
        return
    if result.refusal is Refusal.WRONG_BRANCH:
        if not _has(findings, FindingReason.WRONG_BRANCH):
            findings.append(_derived(FindingReason.WRONG_BRANCH, result.detail))
        return
    if not _has(findings, FindingReason.PUSH_REFUSED):
        data = {"refusal": result.refusal.value} if result.refusal is not None else {}
        if result.commits:
            data["commits"] = list(result.commits)
        findings.append(_derived(FindingReason.PUSH_REFUSED, result.detail, data))


def _refresh_stored(item: Item, findings: list[Finding], planned: _Planned | None) -> None:
    if planned is None:
        return
    # An offline replan cannot see a moved remote, so it may not clear a
    # refusal publish recorded after a real fetch.
    if planned.tree.observed:
        _drop(findings, FindingReason.PUSH_REFUSED)
        _drop(findings, FindingReason.WRONG_BRANCH)
    if batch.publish.already_published(item, planned.tree) and planned.plan.ok:
        _drop(findings, FindingReason.STALE_GENERATED)
        _drop(findings, FindingReason.CONFLICT_HANDWRITTEN)
        _drop(findings, FindingReason.CONFLICT_GENERATED)
        return
    _refresh_rebase(item, findings, planned)
    findings[:] = [f for f in findings
                   if f.reason is not FindingReason.STALE_GENERATED
                   or not _stale_resolved(item, f, planned.tree)]


def _refresh_rebase(item: Item, findings: list[Finding], planned: _Planned) -> None:
    state = batch.plan.pr_state(item.repo_dir, item.branch)
    if not _rebase_clean(state, item, planned.tree):
        return
    _drop(findings, FindingReason.CONFLICT_HANDWRITTEN)
    _drop(findings, FindingReason.CONFLICT_GENERATED)
    stale = list(dict.fromkeys([*state.rebase.files_one_sided, *state.rebase.files_stale]))
    if stale and not _has(findings, FindingReason.STALE_GENERATED):
        findings.append(Finding(
            FindingReason.STALE_GENERATED, "rebase", files=list(stale),
            data={"one_sided": bool(state.rebase.files_one_sided)},
            created_at=state.rebase.updated_at))


def _rebase_clean(state, item: Item, tree: TreeState) -> bool:
    if state is None:
        return False
    status = getattr(state.rebase.status, "value", state.rebase.status)
    return (status in ("clean", "completed")
            and not state.rebase.aborted
            and tree.local not in ("", item.remote_sha, item.pre_rebase_head))


def _stale_resolved(item: Item, finding: Finding, tree: TreeState) -> bool:
    wt = item.worktree
    local = tree.local
    if not wt or not local or not Path(wt).is_dir():
        return False
    rebase_head = str(finding.data.get("rebase_head") or "")
    if rebase_head:
        return (local != rebase_head
                and git.client.ok("merge-base", "--is-ancestor", rebase_head, local, cwd=wt)
                and _touches(wt, finding.files, f"{rebase_head}..{local}"))
    if not finding.created_at or local in (item.remote_sha, item.pre_rebase_head):
        return False
    committed_at = git.client.out("log", "-1", "--format=%cI", local, cwd=wt)
    try:
        later = datetime.fromisoformat(committed_at) > datetime.fromisoformat(finding.created_at)
    except ValueError:
        return False
    return later and _touches(wt, finding.files, local, since=finding.created_at)


def _touches(wt: str, files: list[str], rev: str, *, since: str = "") -> bool:
    """Whether commits in *rev* changed every one of *files*."""
    if not files:
        return False
    args = ["log", "--format=", "--name-only"]
    if since:
        args.append(f"--since={since}")
    r = git.client.run(*args, rev, cwd=wt)
    return r.ok and set(files) <= set(r.stdout.split("\n"))


def _after(run: Run, item: Item) -> list[str]:
    if not item.stacked_on:
        return []
    try:
        base = run.item(item.stacked_on)
    except KeyError:
        return []
    return [item.stacked_on] if not base.terminal else []


def severity_for(findings: list[Finding]) -> Severity:
    if not findings:
        return Severity.ACT
    return min((severity_of(f) for f in findings), key=lambda s: _SEV_RANK[s])


def _has(findings: list[Finding], reason: FindingReason) -> bool:
    return any(f.reason is reason for f in findings)


def _drop(findings: list[Finding], reason: FindingReason) -> None:
    findings[:] = [f for f in findings if f.reason is not reason]


def _commands(run: Run, item: Item, findings: list[Finding],
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
            action="push",
            push=True,
        ))
    if (planned is not None and planned.plan.refusal is Refusal.NOT_INCORPORATED_REMOTE
            and item.remote_sha and (repo := _repo_args(item, checkout))):
        nv = ("--no-verify",) if run.no_verify else ()
        commands.append(SessionCommand(
            ("pr", "rebase", "--push-only", "--expect", item.remote_sha, *nv, *repo),
            "push under the planned lease once listed remote commits are confirmed",
            action="push", push=True))
    if filing := _filing_command(item, checkout):
        commands.append(filing)
    if filing := _review_filing_command(item, findings, checkout):
        commands.append(filing)
    return commands


def _command_for(item: Item, finding: Finding, checkout: str | None) -> SessionCommand | None:
    repo = _repo_args(item, checkout)
    if finding.reason is FindingReason.CONFLICT_HANDWRITTEN and repo:
        return SessionCommand(("pr", "rebase", "--fix", "--no-push", *repo),
                              "resolve hand-written conflicts", action="rebase")
    if finding.reason is FindingReason.CONFLICT_GENERATED and repo:
        return SessionCommand(
            ("pr", "rebase", "--fix", "--no-push", *repo),
            "rebuild generated files, or declare `rebase.regenerate` and run `pr rebase --no-push`",
            action="rebase_generated",
        )
    if finding.reason.value in pr.needs_person.NEXT_REASONS and repo:
        nxt = pr.needs_person.next_command(finding.reason.value, repo,
                                           finding.data.get("jobs") or ())
        return SessionCommand(nxt.argv, nxt.why, action=nxt.action)
    if finding.reason is FindingReason.REVIEW_FINDINGS and repo:
        return SessionCommand(("pr", "review", "--self", "--fix", *repo),
                              "address open review findings", action="review")
    if finding.reason is FindingReason.WRONG_BRANCH:
        if git_cmd := _git(item, checkout, "switch", item.branch):
            return SessionCommand(git_cmd, f"check out {item.branch}",
                                  action="switch_branch")
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
        action="file_review_issue",
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
        action="file_threads_issue",
        files_issue=True,
    )


def _stale_note(finding: Finding) -> str:
    target = ", ".join(finding.files) if finding.files else "the affected files"
    if finding.data.get("one_sided"):
        return (f"verify one-sided resolution in {target} against both sides "
                f"and commit any missing hunks before pushing")
    return f"regenerate {target} at the rebased tip and commit any changes before pushing"


def _notes(item: Item, findings: list[Finding], commands: list[SessionCommand],
           checkout: str | None) -> list[str]:
    notes: list[str] = []
    if any(c.push for c in commands) and not item.is_draft:
        notes.append(COMMENT_BEFORE_PUSH)
    if checkout is None:
        notes.append(NO_CHECKOUT)
    for finding in findings:
        if finding.reason is FindingReason.STALE_GENERATED:
            notes.append(_stale_note(finding))
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
    return pr.needs_person.repo_args(checkout, "" if checkout == item.worktree else item.branch)


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
