"""Builders the review pipeline suites share: a big-PR dict, a trail, a job, args.

The orchestrate path is driven from these rather than from argparse, so both
suites stay coupled to one double of the real parser's namespace.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from agent.types import EFFORT_PRESETS  # noqa: E402
from core.phases import Effort  # noqa: E402
from gh.types import PRContext, PRMetadata  # noqa: E402
from review.types import ReviewJob  # noqa: E402

# Derived from the preset the jobs here run at, so a changed threshold moves
# these with it rather than leaving a suite that passes for the wrong reason.
PRESET = EFFORT_PRESETS[Effort.MEDIUM]
BIG_PR = {
    "additions": PRESET.multi_phase_line_threshold * 8,
    "deletions": PRESET.multi_phase_line_threshold * 4,
    "changed_files": PRESET.multi_phase_file_threshold * 12,
}


class Trail:
    """Records the decisions a run reports, which is where the basis shows up."""

    def __init__(self):
        self.decisions: list[tuple[str, str, dict]] = []

    def decision(self, name, detail, reason="", data=None):
        self.decisions.append((name, reason, data or {}))

    def info(self, *_args, **_kwargs):
        pass

    def span(self, *_args, **_kwargs):
        from contextlib import nullcontext
        return nullcontext()

    def named(self, name: str) -> dict:
        return next(d for n, _, d in self.decisions if n == name)

    def has(self, name: str) -> bool:
        return any(n == name for n, _, _ in self.decisions)


def pipeline_job(tmp_path, *, prior_review=None, **pr_overrides) -> ReviewJob:
    pr_fields = {
        "title": "t", "body": "", "head": "feat", "base": "main",
        "head_sha": "abc", "additions": 10, "deletions": 0,
        "changed_files": 1, "files": [{"path": "a.py", "additions": 10, "deletions": 0}],
    }
    pr_fields.update(pr_overrides)
    job = ReviewJob(
        repo="org/repo", pr_number="42", pr=PRMetadata(**pr_fields),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        generator_version="1.0.0", effort=Effort.MEDIUM,
    )
    if prior_review is not None:
        from review.store import StoredReview
        from review.types import ReviewMeta
        job.prior = StoredReview(
            prior_review,
            ReviewMeta(date="2026-01-01", head_sha="0ldc0de"),
        )
    return job


def orchestrate_args(**overrides) -> SimpleNamespace:
    # `effort` is `--effort`, which argparse defaults to None when unpassed.
    # Present here because `_run_phases` reads it for the pipeline decision: a
    # namespace missing a field the real parser always supplies is a double
    # that has drifted from its subject, and the run fails on the attribute
    # rather than on anything the test is about.
    defaults = {
        "max_parallel": 2, "max_cost": 20.0, "max_groups": None,
        "disprove": None, "fix": False, "effort": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)
