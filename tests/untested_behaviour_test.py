"""Pin behaviour recent changes left untested."""

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr_rebase  # noqa: E402
import core.run_lock  # noqa: E402
import fix.ci  # noqa: E402
import fix.comments  # noqa: E402
import rebase.fork_point  # noqa: E402
import rebase.prepush  # noqa: E402

# Doubled braces (f-string / format) and whitespace around the selection.
_REVIEW_AUTHOR_TYPENAME = re.compile(r"author\s*\{\{?\s*login\s+__typename")


@pytest.mark.parametrize(
    "adapter",
    [
        fix.comments.CommentFixAdapter,
        fix.ci.CIFixAdapter,
        rebase.prepush.PrePushFixAdapter,
    ],
    ids=["CommentFixAdapter", "CIFixAdapter", "PrePushFixAdapter"],
)
def test_fix_adapters_do_not_require_tests(adapter):
    """Owner: `fix.engine.FixAdapter.requires_tests` on CommentFixAdapter,
    CIFixAdapter, and PrePushFixAdapter."""
    assert adapter.requires_tests is False


@pytest.mark.parametrize(
    "rel",
    [
        "ai/lib/gh/pr_data.py",
        "ai/lib/batch/plan.py",
        "ai/lib/gh/pr_pages.py",
    ],
)
def test_review_author_selection_requests_typename(rel):
    """Owner: the review `author { login __typename }` selection in each module."""
    source = (REPO_ROOT / rel).read_text()
    assert _REVIEW_AUTHOR_TYPENAME.search(source)


def test_run_lock_scopes_are_target_and_checkout():
    """Owner: `core.run_lock.Scope` — target and checkout; no Global lock."""
    assert {s.value for s in core.run_lock.Scope} == {"target", "checkout"}
    assert not hasattr(core.run_lock, "Global")
    assert not hasattr(core.run_lock, "GLOBAL_LOCK_ENV")


def test_rebase_parser_exposes_fork_point_flag():
    """Owner: `rebase.fork_point.FLAG` among `cli.pr_rebase.build_parser()` options."""
    strings = {
        s
        for a in cli.pr_rebase.build_parser()._actions
        for s in a.option_strings
    }
    assert rebase.fork_point.FLAG in strings
