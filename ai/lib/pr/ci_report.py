"""What a reader is told about a CI run — the JSON on stdout, the dashboard on stderr.

`pr.ci_runs` says what a run is; this module says how it is reported. A person
reads the dashboard, and a skill or a fix pass reads `CIReport`, whose fields
are the published shape of `ci-check`'s stdout — adding one adds a key every
consumer sees, and dropping one takes a key away from all of them.

A `--wait` run reports the same run twice, once while it is still going and
once when it is done, so `completed` and `total` are present on that path and
absent on the single-shot one rather than being reported as zero.
"""

# doc-group: publishing

from __future__ import annotations

from dataclasses import dataclass

import git.client
import pr.ci_failures
import pr.ci_runs

# ── Failure serialization ──────────────────────────────────────────────────


def serialize_failures(
    failures: dict[str, pr.ci_failures.FailureGroup], progression: dict[str, pr.ci_failures.Outcome] | None = None,
) -> list[dict]:
    """Flatten failure groups into the per-item dicts the report carries.

    A group's `job`, `kind` and `failed_step` are repeated onto each of its
    items: a consumer reads one flat list and never has to hold the grouping.
    """
    result = []
    for group in failures.values():
        for item in group.items:
            outcome = progression.get(item.id, pr.ci_failures.Outcome.NEW).value if progression else "new"
            result.append({
                "id": item.id,
                "job": group.job,
                "failed_step": group.failed_step,
                "kind": group.kind.value,
                "annotation": item.annotation,
                "headline": item.headline,
                "file": item.file,
                "line": item.line,
                "diagnosis": item.diagnosis,
                "fix_sha": item.fix_sha,
                "outcome": outcome,
                "source_run_id": item.source_run_id,
                "context": item.context,
            })
    return result


# ── Report ─────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class CIReport:
    """One run as `ci-check` reports it on stdout.

    The failures and their progression are held in the form `pr.ci_failures`
    produced them; the flat dicts are what `to_json` renders. Everything in
    this process that acts on a failure — the fix pass most of all — reads the
    typed item, so the serialized shape has one producer and no readers here.
    """

    repo: str
    branch: str
    pr_number: int | None
    run_id: int
    run_ids: list[int]
    run_number: int
    head_sha: str
    conclusion: str | None
    behind_main: int
    failures: dict[str, pr.ci_failures.FailureGroup]
    progression: dict[str, pr.ci_failures.Outcome]
    resolved_since_prior: list[str]
    completed: int | None = None
    total: int | None = None
    # Why the run's picture may be incomplete. Non-empty means `failures` is
    # what was found rather than what there is, so a consumer must not read
    # an empty list as a pass — `status` and `conclusion` say the same thing
    # less directly, and the skill was reading `failures` alone.
    unread: tuple[str, ...] = ()
    status: str = ""

    @classmethod
    def build(
        cls,
        *,
        repo: str,
        branch: str,
        pr_number: int | None,
        run_state: pr.ci_failures.RunState,
        progression: dict[str, pr.ci_failures.Outcome],
        prior_run: pr.ci_failures.RunState | None,
        run_ids: list[int],
        behind_main: int,
        counts: pr.ci_runs.JobCounts | None = None,
    ) -> "CIReport":
        """Assemble the report for a parsed run.

        `prior_run` is the last run this branch recorded, and the only thing
        read from it is which of its failures are gone — an id the prior run
        carried and this one does not is one the branch fixed. `counts` is the
        job tally when the caller polled for the run and `None` when it read a
        finished one.
        """
        resolved = []
        if prior_run:
            current_item_ids = pr.ci_failures.collect_item_ids(run_state.failures)
            resolved = [
                item_id for item_id in pr.ci_failures.collect_item_ids(prior_run.failures)
                if item_id not in current_item_ids
            ]
        return cls(
            repo=repo,
            branch=branch,
            pr_number=pr_number,
            run_id=run_state.run_id,
            run_ids=run_ids,
            run_number=run_state.run_number,
            head_sha=run_state.head_sha,
            conclusion=run_state.conclusion,
            unread=run_state.unread,
            status=run_state.status,
            behind_main=behind_main,
            failures=run_state.failures,
            progression=progression,
            resolved_since_prior=resolved,
            completed=counts.completed if counts else None,
            total=counts.total if counts else None,
        )

    def to_json(self) -> dict:
        """The report as the dict written to stdout."""
        report = {
            "repo": self.repo,
            "branch": self.branch,
            "pr_number": self.pr_number,
            "run_id": self.run_id,
            "run_ids": self.run_ids,
            "run_number": self.run_number,
            "head_sha": self.head_sha,
            "conclusion": self.conclusion,
            "status": self.status,
            "unread": list(self.unread),
            "behind_main": self.behind_main,
            "failures": serialize_failures(self.failures, self.progression),
            "progression": {k: v.value for k, v in self.progression.items()},
            "resolved_since_prior": self.resolved_since_prior,
        }
        if self.completed is not None:
            report["completed"] = self.completed
        if self.total is not None:
            report["total"] = self.total
        return report


# ── Dashboard ──────────────────────────────────────────────────────────────

_MAX_DASHBOARD_HEADLINES = 5
_MAX_DASHBOARD_ANNOTATION = 120


def render_dashboard(
    run: pr.ci_failures.RunState,
    progression: dict[str, pr.ci_failures.Outcome],
    run_ids: list[int] | None = None,
    show_status: bool = False,
) -> str:
    """Render a human-readable dashboard string for stderr output."""
    # A commit can be checked by something that never ran a workflow, and there
    # is then no run to number. `Run #0` would name one that does not exist.
    header = (f"## CI Run #{run.run_number} " if run.run_number else "## CI Checks ") \
        + f"({git.client.abbrev(run.head_sha)})"
    if show_status:
        suffix = "in progress" if run.status != "completed" else "complete"
        header += f" — {suffix}"
    lines = [header, ""]

    if run_ids and len(run_ids) > 1:
        lines.append(f"Workflow runs: {', '.join(str(r) for r in run_ids)}")
        lines.append("")

    if run.unread:
        # Printed ahead of everything, and never alongside a pass: these are
        # the checks nobody could read, and a report that lists failures it
        # did find while silently omitting what it could not look at is the
        # false green in its quietest form.
        lines.append("Could not read every check on this commit:")
        lines += [f"  - {reason}" for reason in run.unread]
        lines.append("")

    if not run.failures:
        if run.unread:
            lines.append("No failures among the checks that were read.")
        elif run.status != "completed":
            lines.append("Checks still running — results incomplete.")
        elif run.conclusion and run.conclusion != "success":
            # A conclusion with nothing under it to name: a run held for
            # approval, or one whose only failed job produced no reportable
            # item. Reported as what GitHub said rather than as a pass.
            lines.append(f"No failures to name, but the run concluded {run.conclusion}.")
        else:
            lines.append("All checks passed.")
        return "\n".join(lines)

    kind_counts: dict[pr.ci_failures.FailureKind, int] = {}
    for group in run.failures.values():
        kind_counts[group.kind] = kind_counts.get(group.kind, 0) + len(group.items)

    total = sum(kind_counts.values())
    lines.append(f"Failures: {total} total")
    for kind in pr.ci_failures.FailureKind:
        count = kind_counts.get(kind, 0)
        if count:
            lines.append(f"  {kind.value}: {count}")
    lines.append("")

    headline_count = 0
    overflow = 0
    for group in run.failures.values():
        group_headlines: dict[str, int] = {}
        for item in group.items:
            text = item.headline or item.annotation[:_MAX_DASHBOARD_ANNOTATION]
            group_headlines[text] = group_headlines.get(text, 0) + 1

        if not group_headlines:
            continue

        remaining = _MAX_DASHBOARD_HEADLINES - headline_count
        if remaining <= 0:
            overflow += len(group_headlines)
            continue

        job_label = f"{group.job} → {group.failed_step}" if group.failed_step else group.job
        lines.append(f"  {job_label}:")
        for text, count in list(group_headlines.items())[:remaining]:
            suffix = f" (×{count})" if count > 1 else ""
            lines.append(f"    ▸ {text}{suffix}")
            headline_count += 1
        leftover = len(group_headlines) - remaining
        if leftover > 0:
            overflow += leftover
        lines.append("")

    if overflow > 0:
        lines.append(f"  … and {overflow} more")
        lines.append("")

    outcome_counts: dict[pr.ci_failures.Outcome, int] = {}
    for outcome in progression.values():
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    if outcome_counts:
        parts = [
            f"{outcome_counts[o]} {o.value}"
            for o in pr.ci_failures.Outcome if outcome_counts.get(o, 0)
        ]
        lines.append("Progression: " + ", ".join(parts))
        lines.append("")

    return "\n".join(lines)
