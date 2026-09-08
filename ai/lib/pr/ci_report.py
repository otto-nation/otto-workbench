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

from git import client as git_client
from pr import ci_failures as ci
from pr import ci_runs

# ── Failure serialization ──────────────────────────────────────────────────


def serialize_failures(
    failures: dict[str, ci.FailureGroup], progression: dict[str, ci.Outcome] | None = None,
) -> list[dict]:
    """Flatten failure groups into the per-item dicts the report carries.

    A group's `job`, `kind` and `failed_step` are repeated onto each of its
    items: a consumer reads one flat list and never has to hold the grouping.
    """
    result = []
    for group in failures.values():
        for item in group.items:
            outcome = progression.get(item.id, ci.Outcome.NEW).value if progression else "new"
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
    """One run as `ci-check` reports it on stdout."""

    repo: str
    branch: str
    pr_number: int | None
    run_id: int
    run_ids: list[int]
    run_number: int
    head_sha: str
    conclusion: str | None
    behind_main: int
    failures: list[dict]
    progression: dict[str, str]
    resolved_since_prior: list[str]
    completed: int | None = None
    total: int | None = None

    @classmethod
    def build(
        cls,
        *,
        repo: str,
        branch: str,
        pr_number: int | None,
        run_state: ci.RunState,
        progression: dict[str, ci.Outcome],
        prior_run: ci.RunState | None,
        run_ids: list[int],
        behind_main: int,
        counts: ci_runs.JobCounts | None = None,
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
            current_item_ids = ci.collect_item_ids(run_state.failures)
            resolved = [
                item_id for item_id in ci.collect_item_ids(prior_run.failures)
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
            behind_main=behind_main,
            failures=serialize_failures(run_state.failures, progression),
            progression={k: v.value for k, v in progression.items()},
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
            "behind_main": self.behind_main,
            "failures": self.failures,
            "progression": self.progression,
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
    run: ci.RunState,
    progression: dict[str, ci.Outcome],
    run_ids: list[int] | None = None,
    show_status: bool = False,
) -> str:
    """Render a human-readable dashboard string for stderr output."""
    header = f"## CI Run #{run.run_number} ({git_client.abbrev(run.head_sha)})"
    if show_status:
        suffix = "in progress" if run.status != "completed" else "complete"
        header += f" — {suffix}"
    lines = [header, ""]

    if run_ids and len(run_ids) > 1:
        lines.append(f"Workflow runs: {', '.join(str(r) for r in run_ids)}")
        lines.append("")

    if not run.failures:
        if run.status != "completed":
            lines.append("Checks still running — results incomplete.")
        else:
            lines.append("All checks passed.")
        return "\n".join(lines)

    kind_counts: dict[ci.FailureKind, int] = {}
    for group in run.failures.values():
        kind_counts[group.kind] = kind_counts.get(group.kind, 0) + len(group.items)

    total = sum(kind_counts.values())
    lines.append(f"Failures: {total} total")
    for kind in ci.FailureKind:
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

    outcome_counts: dict[ci.Outcome, int] = {}
    for outcome in progression.values():
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1

    if outcome_counts:
        parts = [
            f"{outcome_counts[o]} {o.value}"
            for o in ci.Outcome if outcome_counts.get(o, 0)
        ]
        lines.append("Progression: " + ", ".join(parts))
        lines.append("")

    return "\n".join(lines)
