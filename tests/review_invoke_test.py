"""The argv `review.invoke` builds, and the guards around the spawn.

These assert the whole command line rather than one flag at a time. The seven
tests this replaces each poked a single flag, so a flag dropped from the middle
of the list was invisible to all of them.

When #909 tranche 4 replaces the spawn with an in-process call, this file is
where that lands: the goldens become the kwargs of the call, and the module
under test stays the same.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from review import invoke as review_invoke  # noqa: E402


def _request(tmp_path, **overrides):
    base = dict(
        repo="acme/widget", pr_number="42",
        review_file=tmp_path / "review.md", wt_path="/wt",
        target_dir=tmp_path / "state", session_log="/log",
        bin_dir=Path("/bin"), generator_version="claude-review 1.2.3",
    )
    base.update(overrides)
    return review_invoke.OrchestrateRequest(**base)


def test_the_pr_review_argv_is_exactly_this(tmp_path):
    """A golden for the PR shape: no --mode, no --fix, no --post."""
    argv = review_invoke.build_argv(_request(tmp_path))

    assert argv == [
        "/bin/review-orchestrate",
        "--repo", "acme/widget",
        "--review-file", str(tmp_path / "review.md"),
        "--repo-dir", "/wt",
        "--target-dir", str(tmp_path / "state"),
        "--session-log", "/log",
        "--pr", "42",
        "--max-parallel", "1",
        "--generator-version", "claude-review 1.2.3",
    ]


def test_the_self_review_argv_carries_mode_fix_and_publish(tmp_path):
    """A golden for the self shape, including the flags only self review sets.

    `--post` is not "publish this review": it tells the orchestrate process
    that its fix pass may push, because the publishing gate is process-wide.
    The field is named `may_publish` for that reason — the argv spelling is an
    accident of the flag, and the two should not be confused at a call site.
    """
    argv = review_invoke.build_argv(_request(
        tmp_path, mode="self", fix_pass=True, may_publish=True,
        model="sonnet", max_cost=12.5, effort="high", max_groups=3,
        generated=True, recover_sha="abc1234",
    ))

    assert argv == [
        "/bin/review-orchestrate",
        "--repo", "acme/widget",
        "--review-file", str(tmp_path / "review.md"),
        "--repo-dir", "/wt",
        "--target-dir", str(tmp_path / "state"),
        "--session-log", "/log",
        "--pr", "42",
        "--mode", "self",
        "--max-parallel", "1",
        "--generator-version", "claude-review 1.2.3",
        "--fix",
        "--post",
        "--max-cost", "12.5",
        "--model", "sonnet",
        "--effort", "high",
        "--max-groups", "3",
        "--generated",
        "--recover-sha", "abc1234",
    ]


def test_a_self_review_without_a_pr_omits_the_pr_flag(tmp_path):
    """A branch with no PR still reviews; it just has no number to pass."""
    argv = review_invoke.build_argv(_request(tmp_path, pr_number="", mode="self"))

    assert "--pr" not in argv
    assert argv[argv.index("--mode") + 1] == "self"


def test_an_unset_effort_is_omitted_rather_than_defaulted(tmp_path):
    """No flag means review-orchestrate gets to consult review.effort itself.

    Sending a default here would silently outrank the config file, which is the
    one thing the absent flag exists to avoid.
    """
    assert "--effort" not in review_invoke.build_argv(_request(tmp_path))
    assert "--effort" in review_invoke.build_argv(_request(tmp_path, effort="medium"))


def test_a_prior_review_is_forwarded_only_when_it_exists(tmp_path):
    """The path is a file on disk, and a stale one must not reach the pipeline."""
    prior = tmp_path / "prior.md"

    assert "--prior-review" not in review_invoke.build_argv(
        _request(tmp_path, prior_review_path=str(prior)))

    prior.write_text("## Must fix\n")
    assert "--prior-review" in review_invoke.build_argv(
        _request(tmp_path, prior_review_path=str(prior)))


def test_a_zero_max_cost_is_forwarded_rather_than_dropped(tmp_path):
    """0 is a real cap ("stop now"), not the same as "unset"."""
    argv = review_invoke.build_argv(_request(tmp_path, max_cost=0))

    assert argv[argv.index("--max-cost") + 1] == "0"


def test_disprove_is_forwarded_only_when_explicitly_true(tmp_path):
    """None means "let effort decide", which is not the same as False."""
    assert "--disprove" not in review_invoke.build_argv(_request(tmp_path))
    assert "--disprove" not in review_invoke.build_argv(_request(tmp_path, disprove=False))
    assert "--disprove" in review_invoke.build_argv(_request(tmp_path, disprove=True))


# ── the guards around the spawn ──────────────────────────────────────────────


def _spawn(monkeypatch, returncode):
    def _run(argv, *a, **kw):
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(review_invoke.subprocess, "run", _run)


def test_a_nonzero_return_code_stops_the_run(tmp_path, monkeypatch):
    """Nothing downstream — display, summary, domain write — should happen."""
    (tmp_path / "review.md").write_text("## Must fix\n")
    _spawn(monkeypatch, 1)

    with pytest.raises(SystemExit) as excinfo:
        review_invoke.run(_request(tmp_path))

    assert excinfo.value.code == 1


def test_a_missing_review_file_stops_the_run(tmp_path, monkeypatch):
    """A pipeline can exit 0 and still produce nothing; that is a failed run.

    Separate from the return code because it catches the case the return code
    cannot: a phase that reported success without writing its deliverable.
    """
    _spawn(monkeypatch, 0)

    with pytest.raises(SystemExit) as excinfo:
        review_invoke.run(_request(tmp_path))

    assert excinfo.value.code == 1


def test_a_successful_run_returns_its_wall_clock(tmp_path, monkeypatch):
    """The figure the summary reports is the pipeline's, not the operator's.

    Timed around the spawn alone, so it does not accumulate the time an
    operator spends answering the post prompts that follow.
    """
    (tmp_path / "review.md").write_text("## Must fix\n")
    _spawn(monkeypatch, 0)

    wall_ms = review_invoke.run(_request(tmp_path))

    assert isinstance(wall_ms, int)
    assert wall_ms >= 0
