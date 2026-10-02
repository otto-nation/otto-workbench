"""Fixtures shared by the review_prompt suites: the default budget, a preflight and a job."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from gh.types import PRContext, PRMetadata
from review.types import PreflightData, ReviewJob
from core.phases import Mode

from conftest import model_budget_bytes

# The model every phase resolves to here, and the ceiling it buys. Tests state
# the budget once rather than at each of two dozen call sites; a test that
# cares about a different model passes `budget_bytes` itself.
MAX_PROMPT_BYTES = model_budget_bytes()


def _make_preflight(**overrides):
    defaults = dict(
        diff="", commit_log="", file_contents={"a.py": "x", "b.py": "y"},
        file_permissions={}, claude_md="", architecture_md="",
        omitted_files=[],
        prior_head_sha="abc1234def",
        delta_files=["a.py", "b.py"],
        delta_commit_log="feat: stuff",
        delta_diff=(
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+new\n"
        ),
    )
    defaults.update(overrides)
    return PreflightData(**defaults)


def _make_job(preflight=None, mode=Mode.PR):
    pr = PRMetadata(
        title="T", body="B", head="h", base="main", head_sha="abc",
        additions=10, deletions=5, changed_files=1,
        files=[{"path": "a.py", "additions": 10, "deletions": 5}],
    )
    ctx = PRContext(commits="abc feat")
    return ReviewJob(
        repo="r", pr_number="1", pr=pr, ctx=ctx,
        wt_path="/tmp/w", review_file="/tmp/r.md",
        session_log="/tmp/l.jsonl",
        preflight=preflight, mode=mode,
    )
