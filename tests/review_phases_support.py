"""Shared ReviewJob builders for the phase-runner suites."""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from core.phases import Effort
from gh.types import PRContext, PRMetadata
from review.types import PreflightData, ReviewJob


def _job(tmp_path, effort=Effort.MEDIUM):
    return ReviewJob(
        repo="org/repo", pr_number="42",
        pr=PRMetadata("t", "", "head", "main", "abc123", 100, 5, 3, []),
        ctx=PRContext(), wt_path=str(tmp_path),
        review_file=str(tmp_path / "review.md"),
        session_log=str(tmp_path / "session.jsonl"),
        effort=effort,
    )


def _omitted_job(tmp_path, omitted=(), effort=Effort.MEDIUM):
    job = _job(tmp_path, effort)
    job.preflight = PreflightData(
        diff="", commit_log="", file_contents={}, file_permissions={},
        instructions_md="", architecture_md="", omitted_files=list(omitted),
    )
    return job
