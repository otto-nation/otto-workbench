"""Tests for fix.ci — CI's adapter onto the shared fix engine."""

import sys
from pathlib import Path
from unittest.mock import patch

from conftest import make_ctx

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from fix import ci as fix_ci  # noqa: E402
from fix import engine as fix_engine  # noqa: E402
from git import land  # noqa: E402
from git.land import CommitStatus  # noqa: E402
from pr import ci_failures as ci  # noqa: E402
from pr.ci_report import CIReport  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr.state import PRIdentity, PRState  # noqa: E402


def _item(item_id: str, **kwargs) -> ci.FailureItem:
    defaults = dict(
        annotation=item_id, file=None, line=None, diagnosis=None,
        fix_sha=None, outcome=None, headline=item_id,
    )
    defaults.update(kwargs)
    return ci.FailureItem(id=item_id, **defaults)


def _group(job: str, kind: ci.FailureKind, *items: ci.FailureItem) -> ci.FailureGroup:
    return ci.FailureGroup(job=job, kind=kind, items=items)


def _report(failures: dict[str, ci.FailureGroup], progression=None, run_number=7) -> CIReport:
    """A finished report, as `_run_ci` hands it to the fix phase."""
    return CIReport(
        repo="owner/repo", branch="feat/test", pr_number=42,
        run_id=100, run_ids=[100], run_number=run_number, head_sha="abc123",
        conclusion="failure", behind_main=0, failures=failures,
        progression=progression or {}, resolved_since_prior=[],
    )


def _state():
    return PRState(identity=PRIdentity(
        repo="owner/repo", branch="feat/test", pr_number=42,
        head_sha="abc123", worktree_root="",
    ))


def _adapter(tmp_path, failures, progression=None, run_number=7, state=None):
    """The CI adapter as `_run_fix` builds it, against a real worktree path."""
    return fix_ci.CIFixAdapter(
        _report(failures, progression, run_number),
        make_ctx(worktree_root=tmp_path, target_dir=tmp_path),
        state if state is not None else _state(),
    )


# ── flatten ─────────────────────────────────────────────────────────────


def test_flatten_pairs_each_item_with_the_job_it_failed_in():
    failures = {
        "shellcheck": _group("shellcheck", ci.FailureKind.LINT, _item("a"), _item("b")),
        "pytest": _group("pytest", ci.FailureKind.TEST, _item("c")),
    }

    flat = fix_ci.flatten(failures, {})

    assert [(f.id, f.group.job) for f in flat] == [
        ("a", "shellcheck"), ("b", "shellcheck"), ("c", "pytest"),
    ]


def test_flatten_reports_an_untracked_item_as_new():
    """A run with no prior has no progression entry for anything in it."""
    failures = {"lint": _group("lint", ci.FailureKind.LINT, _item("a"), _item("b"))}

    flat = fix_ci.flatten(failures, {"a": ci.Outcome.PERSISTING})

    assert [f.outcome for f in flat] == [ci.Outcome.PERSISTING, ci.Outcome.NEW]


# ── CIFixAdapter ────────────────────────────────────────────────────────


def test_only_fixable_failures_are_handed_to_the_agent(tmp_path):
    """Infra and flaky failures are held back — no edit would clear either."""
    failures = {
        "shellcheck": _group(
            "shellcheck", ci.FailureKind.LINT,
            _item("lint-1", annotation="SC2086", headline="SC2086",
                  file="bin/foo.sh", line=42),
        ),
        "docker": _group(
            "docker", ci.FailureKind.INFRA,
            _item("infra-1", annotation="connection refused"),
        ),
        "pytest": _group(
            "pytest", ci.FailureKind.FLAKY,
            _item("flaky-1", annotation="timeout", file="tests/slow.py", line=1),
        ),
    }
    adapter = _adapter(tmp_path, failures)

    assert [i.id for i in adapter.items()] == ["lint-1"]
    assert [f.id for f in adapter.skipped] == ["infra-1", "flaky-1"]


def test_each_item_carries_the_failure_the_agent_has_to_read(tmp_path):
    """Location, job and the failure text all reach the checklist entry."""
    failures = {
        "shellcheck": _group(
            "shellcheck", ci.FailureKind.LINT,
            _item("sc2086-bin-foo-42", annotation="SC2086: Double quote",
                  headline="SC2086: Double quote", file="bin/foo.sh", line=42),
        ),
        "pytest": _group(
            "pytest", ci.FailureKind.TEST,
            _item("pytest-test-auth-18", annotation="AssertionError",
                  headline="AssertionError", file="tests/auth.py", line=18),
        ),
    }
    progression = {"pytest-test-auth-18": ci.Outcome.PERSISTING}

    lint, test = _adapter(tmp_path, failures, progression).items()

    assert (lint.file, lint.line, lint.label) == ("bin/foo.sh", 42, "shellcheck")
    assert "SC2086: Double quote" in lint.body
    assert (test.file, test.line, test.label) == ("tests/auth.py", 18, "pytest")
    assert "persisting" in test.body


def test_a_new_failure_says_nothing_about_progression(tmp_path):
    """"new" under a Progression heading is a line that carries no information."""
    failures = {"lint": _group("lint", ci.FailureKind.LINT, _item("a"))}

    body, = [i.body for i in _adapter(tmp_path, failures).items()]

    assert "Progression" not in body


def test_an_item_repeating_its_headline_is_not_detailed_twice(tmp_path):
    """Most annotations are the headline — a second copy under Details is noise."""
    failures = {
        "lint": _group(
            "lint", ci.FailureKind.LINT,
            _item("a", annotation="SC2086", headline="SC2086", context="line 3"),
        ),
    }

    body, = [i.body for i in _adapter(tmp_path, failures).items()]

    assert body.count("SC2086") == 1
    assert "**Context:** line 3" in body


def test_a_failure_with_no_location_still_becomes_an_item(tmp_path):
    """A build failure names no file, and the em dash stands in for one."""
    failures = {
        "gradle": _group(
            "gradle", ci.FailureKind.BUILD,
            _item("build-1", annotation="compilation failed"),
        ),
    }

    item, = _adapter(tmp_path, failures).items()

    assert (item.file, item.line) == ("", 0)
    assert item.location() == "—"


def test_a_run_of_only_skipped_failures_hands_over_nothing(tmp_path):
    """`_run_fix` reads `fixable` to decide there is nothing to ask an agent."""
    failures = {
        "docker": _group("docker", ci.FailureKind.INFRA, _item("infra-1", annotation="OOM")),
    }
    adapter = _adapter(tmp_path, failures)

    assert adapter.fixable == []
    assert adapter.items() == []


def test_the_tracking_file_is_named_for_the_run(tmp_path):
    """One directory per pass holds both the checklist and the session log."""
    adapter = _adapter(tmp_path, {}, run_number=11)

    assert adapter.title == "CI Fix Tracking — Run #11"
    assert adapter.tracking_path == tmp_path / "ignore" / "ci-failures" / "fix-tracking.md"
    assert adapter.session_log == tmp_path / "ignore" / "ci-failures" / "fix-session.jsonl"


def test_the_commit_message_counts_what_the_agent_answered(tmp_path):
    """Fixed and not-fixed, so the log says what a pass achieved without a diff."""
    adapter = _adapter(tmp_path, {})
    outcomes = [
        ItemOutcome(id="a", outcome=FixOutcome.FIXED),
        ItemOutcome(id="b", outcome=FixOutcome.DECLINED),
        ItemOutcome(id="c", outcome=FixOutcome.DEFERRED),
    ]

    spec = adapter.landing(outcomes)

    assert spec.message == "fix: address CI failures\n\n1 fixed, 2 skipped"
    assert spec.regen == "chore: regenerate after CI fixes"


def test_a_pass_that_fixed_nothing_says_only_what_it_did(tmp_path):
    """No counts line — "0 fixed, 3 skipped" is noise in a log."""
    adapter = _adapter(tmp_path, {})
    outcomes = [ItemOutcome(id="a", outcome=FixOutcome.DECLINED)]

    assert adapter.landing(outcomes).message == "fix: address CI failures"


def test_held_back_failures_are_recorded_as_skipped(tmp_path):
    """A record holding only the agent's answers reads as if infra was never seen."""
    failures = {
        "shellcheck": _group(
            "shellcheck", ci.FailureKind.LINT,
            _item("lint-1", annotation="SC2086", file="bin/foo.sh", line=42),
        ),
        "docker": _group(
            "docker", ci.FailureKind.INFRA,
            _item("infra-1", annotation="connection refused"),
        ),
    }
    state = _state()
    adapter = _adapter(tmp_path, failures, state=state)
    run = fix_engine.FixRun(
        outcomes=[ItemOutcome(id="lint-1", outcome=FixOutcome.FIXED)],
        landed=land.LandResult(CommitStatus.PUSH_HELD, "deadbee"),
        head_before="cafe123",
    )

    with patch.object(fix_ci.pr_state, "save_state") as saved:
        adapter.record(run)

    recorded = {i.id: i for i in state.ci.fix.items}
    assert recorded["lint-1"].outcome is FixOutcome.FIXED
    assert recorded["infra-1"].outcome is FixOutcome.SKIPPED
    assert "infra" in recorded["infra-1"].reason
    assert recorded["infra-1"].read_sha == "cafe123"
    assert state.ci.fix.commit_sha == "deadbee"
    assert saved.called
