"""Tests for supersession: is this branch's reason to exist already gone?

The branch is fixing code `main` has already deleted. Each
signal is driven on its own, because the value of the preflight is that any one
of them can fire — a branch can be superseded without having been rebased, and
a rebase is not on its own a reason to withhold anything.
"""

import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import pr_state
import supersession
from pr_state import SupersessionDomain, SupersessionKind, SupersessionSignal

_CLEAN_LOG = "1700000000 1700000000\n"
_SKEWED_LOG = "1700000000 1700864000\n"
_READDS_DIFF = "+++ b/ai/lib/foo.py\n+def dropped_helper(x):\n     pass\n"
_HEAD_SHA = "a" * 40
_BASE_SHA = "b" * 40


def _completed(returncode, stdout=""):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr="")


def _subcommand(cmd) -> str:
    """The git subcommand, past any `-c key=value` the client prefixes it with."""
    rest = cmd[1:]
    while rest[:1] == ["-c"]:
        rest = rest[2:]
    return rest[0] if rest else ""


def _git_stub(*, log_out=_CLEAN_LOG, diff="", grep_rc=0, pickaxe="",
              gh_out="", gh_rc=0, calls=None):
    """A `subprocess.run` stand-in answering every call the preflight makes.

    Dispatch is on the git subcommand rather than on call order, so a test that
    only cares about the pickaxe does not have to know how many `rev-parse`
    calls precede it.
    """
    def run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        if cmd[0] == "gh":
            return _completed(gh_rc, stdout=gh_out)
        sub = _subcommand(cmd)
        if sub == "rev-parse":
            ref = cmd[cmd.index("rev-parse") + 1]
            return _completed(0, stdout=(_HEAD_SHA if ref == "HEAD"
                                         else _BASE_SHA) + "\n")
        if "--reverse" in cmd:
            return _completed(0, stdout=log_out)
        if sub == "diff":
            return _completed(0, stdout=diff)
        if sub == "grep":
            return _completed(grep_rc)
        return _completed(0, stdout=pickaxe)
    return run


def _detect(**kwargs):
    """`detect` against a stubbed git, with the default branch already known."""
    with patch.object(supersession.subprocess, "run",
                      side_effect=_git_stub(**kwargs)):
        return supersession.detect(Path("/fake"), "owner/repo", base="origin/main")


def _signals(**kwargs):
    return _detect(**kwargs).signals


# ── Detection ───────────────────────────────────────────────────────────────


class TestDetect:
    def test_a_healthy_branch_raises_nothing(self):
        assert _signals() == []

    def test_rebase_skew_is_reported(self):
        signals = _signals(log_out=_SKEWED_LOG)
        assert [s.kind for s in signals] == [SupersessionKind.REBASE_SKEW]
        assert "10 day(s)" in signals[0].detail

    def test_rebase_skew_alone_does_not_hold(self):
        """Every long-lived branch has one. Holding on it would fire on health."""
        assert _signals(log_out=_SKEWED_LOG)[0].holds is False

    def test_a_same_week_rebase_is_not_skew(self):
        assert _signals(log_out="1700000000 1700100000\n") == []

    def test_unreadable_dates_are_not_a_finding(self):
        """A hint that cannot be computed is not evidence of anything."""
        assert _signals(log_out="not a timestamp\n") == []

    def test_a_readded_symbol_is_reported(self):
        signals = _signals(diff=_READDS_DIFF, grep_rc=1, pickaxe="abc1234\n")
        assert [s.kind for s in signals] == [
            SupersessionKind.READDS_REMOVED_SYMBOL,
        ]
        assert "dropped_helper" in signals[0].detail
        assert "abc1234" in signals[0].detail

    def test_a_readded_symbol_holds(self):
        signals = _signals(diff=_READDS_DIFF, grep_rc=1, pickaxe="abc1234\n")
        assert signals[0].holds is True

    def test_a_symbol_the_base_still_has_is_not_readded(self):
        assert _signals(diff=_READDS_DIFF, grep_rc=0) == []

    def test_a_symbol_the_base_never_had_is_just_new(self):
        """Absent from the base with no history of it is what new code looks like."""
        assert _signals(diff=_READDS_DIFF, grep_rc=1, pickaxe="") == []

    def test_the_superseding_pr_is_surfaced(self):
        signals = _signals(
            diff=_READDS_DIFF, grep_rc=1, pickaxe="abc1234\n",
            gh_out="#42 refactor: drop dropped_helper\n",
        )
        assert [s.kind for s in signals] == [
            SupersessionKind.READDS_REMOVED_SYMBOL,
            SupersessionKind.SUPERSEDING_PR,
        ]
        assert "#42 refactor: drop dropped_helper" in signals[1].detail

    def test_a_failed_search_still_leaves_the_local_signal(self):
        """No network is a reason to say less, not a reason to say nothing."""
        signals = _signals(
            diff=_READDS_DIFF, grep_rc=1, pickaxe="abc1234\n", gh_rc=1,
        )
        assert [s.kind for s in signals] == [
            SupersessionKind.READDS_REMOVED_SYMBOL,
        ]

    def test_nothing_is_searched_for_when_nothing_was_readded(self):
        """This is a preflight — the network call is earned, not routine."""
        calls = []
        _detect(diff=_READDS_DIFF, grep_rc=0, calls=calls)
        assert [c for c in calls if c[0] == "gh"] == []

    def test_the_symbol_scan_is_capped(self):
        diff = "+++ b/ai/lib/foo.py\n" + "".join(
            f"+def helper_{n}(x):\n" for n in range(25)
        )
        signals = _signals(diff=diff, grep_rc=1, pickaxe="abc1234\n")
        readded = [s for s in signals
                   if s.kind == SupersessionKind.READDS_REMOVED_SYMBOL]
        assert len(readded) == supersession._PREFLIGHT_SYMBOL_LIMIT

    def test_the_search_is_capped_harder(self):
        diff = "+++ b/ai/lib/foo.py\n" + "".join(
            f"+def helper_{n}(x):\n" for n in range(25)
        )
        calls = []
        _detect(diff=diff, grep_rc=1, pickaxe="abc1234\n",
                gh_out="#42 t\n", calls=calls)
        assert (len([c for c in calls if c[0] == "gh"])
                == supersession._PREFLIGHT_SEARCH_LIMIT)

    def test_the_findings_reach_the_trail(self):
        trail = MagicMock()
        with patch.object(supersession.subprocess, "run",
                          side_effect=_git_stub(log_out=_SKEWED_LOG)):
            supersession.detect(Path("/fake"), "owner/repo",
                                base="origin/main", trail=trail)
        assert trail.info.call_args.kwargs["data"]["signals"] == [
            SupersessionKind.REBASE_SKEW,
        ]

    def test_the_base_is_resolved_when_the_caller_does_not_know_it(self):
        """The one caller that has already paid for it passes it; the rest don't."""
        with patch.object(supersession.pr_context, "default_branch",
                          return_value="trunk") as resolve, \
             patch.object(supersession.subprocess, "run",
                          side_effect=_git_stub()):
            supersession.detect(Path("/fake"), "owner/repo")
        assert resolve.called

    def test_the_verdict_carries_the_shas_it_was_computed_against(self):
        verdict = _detect()
        assert (verdict.head_sha, verdict.base_sha) == (_HEAD_SHA, _BASE_SHA)

    def test_an_unresolvable_ref_leaves_the_sha_empty(self):
        """Empty is what keeps a verdict computed against nothing from being reused."""
        with patch.object(supersession.subprocess, "run",
                          return_value=_completed(1)):
            verdict = supersession.detect(Path("/fake"), "owner/repo",
                                          base="origin/main")
        assert (verdict.head_sha, verdict.base_sha) == ("", "")


# ── Verdict ─────────────────────────────────────────────────────────────────


class TestVerdict:
    def test_nothing_found_is_not_superseded(self):
        assert supersession.Verdict().superseded is False

    def test_context_alone_is_not_superseded(self):
        verdict = supersession.Verdict([SupersessionSignal(
            SupersessionKind.REBASE_SKEW, "d", holds=False,
        )])
        assert verdict.superseded is False
        assert verdict.holding == []

    def test_evidence_is_superseded(self):
        holds = SupersessionSignal(SupersessionKind.READDS_REMOVED_SYMBOL, "d")
        verdict = supersession.Verdict([
            SupersessionSignal(SupersessionKind.REBASE_SKEW, "d", holds=False),
            holds,
        ])
        assert verdict.superseded is True
        assert verdict.holding == [holds]


# ── The cache ───────────────────────────────────────────────────────────────


def _state_with(tmp_path, domain: SupersessionDomain) -> Path:
    state = pr_state.new_state("owner/repo", "feat/x", 1, _HEAD_SHA, "/wt")
    pr_state.apply(state, domain)
    pr_state.save_state(tmp_path, state)
    return tmp_path


def _detect_cached(target_dir, **kwargs):
    calls = kwargs.pop("calls", [])
    with patch.object(supersession.subprocess, "run",
                      side_effect=_git_stub(calls=calls, **kwargs)):
        return supersession.detect_cached(
            Path("/fake"), "owner/repo", target_dir, base="origin/main",
        )


class TestDetectCached:
    _CACHED = SupersessionSignal(
        SupersessionKind.SUPERSEDING_PR, "#42 from the state file",
    )

    def _stored(self, tmp_path, **overrides):
        fields = dict(head_sha=_HEAD_SHA, base_sha=_BASE_SHA,
                      signals=[self._CACHED])
        fields.update(overrides)
        return _state_with(tmp_path, SupersessionDomain(**fields))

    def test_a_matching_verdict_is_reused(self, tmp_path):
        verdict = _detect_cached(self._stored(tmp_path))
        assert verdict.signals == [self._CACHED]

    def test_a_matching_verdict_costs_no_search(self, tmp_path):
        """The network call is the whole reason this cache exists."""
        calls = []
        _detect_cached(self._stored(tmp_path), calls=calls,
                       diff=_READDS_DIFF, grep_rc=1, pickaxe="abc1234\n",
                       gh_out="#7 t\n")
        assert [c for c in calls if c[0] == "gh"] == []

    def test_a_moved_head_recomputes(self, tmp_path):
        verdict = _detect_cached(self._stored(tmp_path, head_sha="c" * 40))
        assert verdict.signals == []

    def test_a_moved_base_recomputes(self, tmp_path):
        """A branch re-adds nothing until the hour `main` deletes something."""
        verdict = _detect_cached(
            self._stored(tmp_path, base_sha="c" * 40),
            diff=_READDS_DIFF, grep_rc=1, pickaxe="abc1234\n",
        )
        assert [s.kind for s in verdict.signals] == [
            SupersessionKind.READDS_REMOVED_SYMBOL,
        ]

    def test_a_verdict_keyed_on_nothing_is_never_reused(self, tmp_path):
        """Empty SHAs are what an unresolvable ref stored — not a cache key."""
        with patch.object(supersession.subprocess, "run",
                          return_value=_completed(1)):
            verdict = supersession.detect_cached(
                Path("/fake"), "owner/repo",
                self._stored(tmp_path, head_sha="", base_sha=""),
                base="origin/main",
            )
        assert verdict.signals == []

    def test_a_fresh_verdict_is_written_back(self, tmp_path):
        _state_with(tmp_path, SupersessionDomain())
        _detect_cached(tmp_path, diff=_READDS_DIFF, grep_rc=1,
                       pickaxe="abc1234\n")
        stored = pr_state.load_state(tmp_path).supersession
        assert stored.matches(_HEAD_SHA, _BASE_SHA)
        assert [s.kind for s in stored.signals] == [
            SupersessionKind.READDS_REMOVED_SYMBOL,
        ]

    def test_no_state_file_yet_computes_and_stores_nothing(self, tmp_path):
        """The command that owns the state writes it moments later anyway."""
        verdict = _detect_cached(tmp_path, log_out=_SKEWED_LOG)
        assert [s.kind for s in verdict.signals] == [
            SupersessionKind.REBASE_SKEW,
        ]
        assert pr_state.load_state(tmp_path) is None

    def test_no_target_dir_at_all_still_answers(self, tmp_path):
        """`pr comments` outside a resolved target is still owed a verdict."""
        verdict = _detect_cached(None, log_out=_SKEWED_LOG)
        assert [s.kind for s in verdict.signals] == [
            SupersessionKind.REBASE_SKEW,
        ]

    def test_the_reuse_is_recorded_on_the_trail(self, tmp_path):
        trail = MagicMock()
        with patch.object(supersession.subprocess, "run",
                          side_effect=_git_stub()):
            supersession.detect_cached(
                Path("/fake"), "owner/repo", self._stored(tmp_path),
                base="origin/main", trail=trail,
            )
        assert trail.info.call_args.kwargs["data"]["head_sha"] == _HEAD_SHA


# ── Reporting ───────────────────────────────────────────────────────────────


class TestReport:
    def test_nothing_found_says_nothing(self, capsys):
        supersession.report(supersession.Verdict())
        assert capsys.readouterr().err == ""

    def test_the_output_names_the_signal_that_fired(self, capsys):
        supersession.report(supersession.Verdict([
            SupersessionSignal(SupersessionKind.REBASE_SKEW,
                               "replayed onto a moved base", holds=False),
            SupersessionSignal(SupersessionKind.READDS_REMOVED_SYMBOL,
                               "`foo` is gone from origin/main"),
        ]))
        err = capsys.readouterr().err
        assert "[rebase_skew] replayed onto a moved base" in err
        assert "[readds_removed_symbol] `foo` is gone from origin/main" in err

    def test_context_alone_is_still_printed(self, capsys):
        """It does not hold anything, but it is why the branch looks the way it does."""
        supersession.report(supersession.Verdict([SupersessionSignal(
            SupersessionKind.REBASE_SKEW, "replayed", holds=False,
        )]))
        assert "[rebase_skew] replayed" in capsys.readouterr().err
