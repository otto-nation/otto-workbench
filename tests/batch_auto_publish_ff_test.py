"""--auto-publish ff pushes a clean fast-forward only.

Revert targets are named in each test.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from batch.model import Finding, FindingReason, ItemStatus, Step  # noqa: E402
from batch.publish import TreeState  # noqa: E402
from pr.comments_fix import CloseoutDebt  # noqa: E402
from rebase.types import RefDivergence  # noqa: E402
# `_close_open_sessions` and `_quiet_outcomes` are autouse fixtures.
from batch_scheduler_support import (  # noqa: F401,E402
    NEED, Harness, _close_open_sessions, _quiet_outcomes, ff_tree, row,
)


def _ff_run(**kw):
    h = Harness([row(1, {Step.CI: NEED})], schema_version=2, with_ai=[Step.CI],
                moves={("ci", "/wt/b1")}, **kw)
    h.run.auto_publish_ff = True
    return h


def diverged_tree(item):
    return TreeState(local=f"{item.remote_sha}+1", remote=item.remote_sha,
                     divergence=RefDivergence(ahead=1, behind=1, comparable=True))


def test_ff_pushes_a_clean_fast_forward():
    # Revert: drop the commands[0][1] == "push" check (the next case fails).
    h = _ff_run(tree=ff_tree)
    h.sched.run_until_blocked()
    item = h.run.items[0]
    assert h.published
    push = h.published[0]
    assert push[:3] == ["pr", "push", "--expect"]
    assert push[3] == "h"
    assert push[-2:] == ["--repo-dir", "/wt/b1"]
    assert item.status is ItemStatus.DONE
    assert item.findings == []
    assert item.published_sha == "h+1"


def test_ff_never_force_pushes_a_diverged_branch():
    # Revert: drop the commands[0][1] == "push" check.
    h = _ff_run(tree=diverged_tree)
    h.sched.run_until_blocked()
    assert h.published == []
    assert any(f.reason is FindingReason.READY_TO_PUSH for f in h.run.items[0].findings)


def test_ff_never_posts_the_closeout():
    # Revert: pass the full debt to plan.
    needs = {Step.CI: NEED, Step.COMMENTS: NEED}
    h = Harness([row(1, needs)], schema_version=2,
                with_ai=[Step.CI, Step.COMMENTS], moves={("ci", "/wt/b1")},
                closeout=lambda d, b: CloseoutDebt(summary=True), tree=ff_tree)
    h.run.auto_publish_ff = True
    h.sched.run_until_blocked()
    assert h.published
    assert all(a[1] != "comments" for a in h.published)
    assert any(f.reason is FindingReason.CLOSEOUT_OWED for f in h.run.items[0].findings)


def test_ff_skips_an_item_with_findings():
    # Revert: drop the item.findings guard.
    h = _ff_run(tree=ff_tree)
    h.run.items[0].findings.append(Finding(reason=FindingReason.CI_FAILING, step="ci"))
    h.sched.run_until_blocked()
    assert h.published == []


def test_a_failed_ff_push_is_a_finding():
    # Revert: treat a non-zero exit as PUSHED.
    published = []

    def fail(argv, log_path=None):
        published.append(argv)
        if log_path is not None:
            Path(log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(log_path).write_text("$ pr push\npush refused (hook)\n")
        return 1

    h = _ff_run(tree=ff_tree, runner=fail)
    h.sched.run_until_blocked()
    item = h.run.items[0]
    assert published
    assert item.status is ItemStatus.NEEDS_SESSION
    assert item.findings[0].reason in (
        FindingReason.PUSH_REFUSED, FindingReason.FAILED_UNKNOWN)


# passes-at-base: the default path this change must not alter
def test_no_ff_without_the_flag():
    h = Harness([row(1, {Step.CI: NEED})], schema_version=2, with_ai=[Step.CI],
                moves={("ci", "/wt/b1")}, tree=ff_tree)
    h.sched.run_until_blocked()
    assert h.published == []
