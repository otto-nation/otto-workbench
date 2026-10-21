"""Tests for prior-findings reconciliation.

Two halves. The document half asks what the review said about each prior
finding, and needs no tree — those tests pass no worktree, which is also the
configuration in which nothing is inferred. The tree half asks what the code
says when the review said nothing, and runs against a real repo with two
commits, because the whole question is what `git show <prior>:<path>` holds
that the working tree no longer does.
"""

import json
import sys
from pathlib import Path

import pytest
from conftest import git_out

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import review.reconcile
from review.document import SECTION_PRIOR_FINDINGS
from review.store import StoredReview
from review.types import ReviewMeta
from review_record_support import finding, record

from review.paths import FILENAME_PRIOR_FINDINGS
from review.grammar import FindingIdentity
from review.reconcile import DispositionSource, UndecidedReason, _prior_findings, reconcile
from review.record import LedgerLine, RecordFinding, ReviewRecord
from review.types import FindingRef, PriorDisposition

F_M1 = finding("M1", "missing error check", path="handler.go", line=42)
PRIOR_ONE_FINDING = record(F_M1)

_BEFORE = (
    "package main\n"
    "\n"
    "func handle() {\n"
    "    rows, _ := db.Query(sql)\n"
    "    defer rows.Close()\n"
    "}\n"
)

_AFTER = (
    "package main\n"
    "\n"
    "func handle() error {\n"
    "    rows, err := db.Query(sql)\n"
    "    if err != nil {\n"
    "        return err\n"
    "    }\n"
    "    defer rows.Close()\n"
    "    return nil\n"
    "}\n"
)


def _commit(wt: Path, message: str) -> str:
    git_out(wt, "add", "-A")
    git_out(wt, "commit", "-qm", message)
    return git_out(wt, "rev-parse", "HEAD").strip()


@pytest.fixture
def repo(tmp_path):
    """An empty repo whose commits cannot reach the developer's own hooks."""
    wt = tmp_path / "worktree"
    wt.mkdir()
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    git_out(wt, "init", "-q", "-b", "main")
    git_out(wt, "config", "user.email", "test@example.com")
    git_out(wt, "config", "user.name", "Test")
    git_out(wt, "config", "commit.gpgsign", "false")
    git_out(wt, "config", "core.hooksPath", str(hooks))
    return wt


def _prior(*findings, rec=None, sha: str = "", date: str = "", **fields) -> StoredReview:
    """A prior round as data: the record plus the sidecar facts reconciliation reads."""
    body = rec if rec is not None else record(*findings, **fields)
    return StoredReview(body, ReviewMeta(head_sha=sha, date=date))


def _rev(*findings, **fields):
    return record(*findings, **fields)


def _ledger(*entries: LedgerLine):
    return record(ledger=entries)


def _by_id(reconciliation, finding_id: str):
    return next(r for r in reconciliation.records if r.ref.finding_id == finding_id)


# ── Reading the prior review's findings ──────────────────────────────────────


class TestParsePriorFindings:
    """How `_prior_findings` reads a prior record before reconciling it."""

    def test_a_findings_quotation_below_its_first_line_travels_with_it(self):
        first, second = review.reconcile._prior_findings(_prior(
            finding(
                "M1", "missing error check\n  The call reads `rows, _ := db.Query(sql)` today.",
                path="handler.go", line=42,
            ),
            finding("M2", "stale entry", path="cache.go", line=9),
        ).record)
        assert "rows, _ := db.Query(sql)" in first.text
        assert "rows, _ := db.Query(sql)" not in second.text

    def test_a_findings_text_stops_at_the_next_section(self):
        first = review.reconcile._prior_findings(
            _prior(F_M1, verdict="Request changes.").record,
        )[0]
        assert "Request changes" not in first.text


# ── What the review itself accounts for ──────────────────────────────────────


class TestAccountedByTheReview:
    def test_empty_prior_reconciles_nothing(self):
        assert review.reconcile.reconcile(_prior(), _rev()).records == []

    def test_sid_marker_carries_finding_forward(self):
        sid = FindingIdentity("handler.go", None, "missing error check").stable_id
        rec = _by_id(review.reconcile.reconcile(
            _prior(F_M1),
            _rev(finding("M1", "reworded", path="other.go", sid=sid)),
        ), "M1")
        assert rec.disposition is PriorDisposition.STILL_OPEN
        assert rec.source is DispositionSource.CARRIED

    def test_verbatim_carry_forward_without_marker(self):
        assert review.reconcile.reconcile(
            _prior(F_M1),
            _rev(finding("M2", "missing error check", path="handler.go", line=42)),
        ).unaccounted == []

    def test_ledger_entry_accounts_for_fixed_finding(self):
        rec = _by_id(review.reconcile.reconcile(
            _prior(F_M1),
            _ledger(LedgerLine("M1", PriorDisposition.FIXED, "handler.go")),
        ), "M1")
        assert rec.disposition is PriorDisposition.FIXED
        assert rec.source is DispositionSource.LEDGER

    def test_a_verdict_ending_a_sentence_is_read_as_that_verdict(self):
        """The ledger form a synthesis agent writes when its detail is prose."""
        rec = _by_id(review.reconcile.reconcile(
            _prior(F_M1),
            _ledger(LedgerLine(
                "M1", PriorDisposition.FIXED, "handler.go",
                "The error is checked now.",
            )),
        ), "M1")
        assert rec.disposition is PriorDisposition.FIXED
        assert rec.source is DispositionSource.LEDGER

    def test_a_prior_finding_citing_a_bare_file_is_still_matched(self):
        """A location a review wrote without a line number still names its file."""
        n9 = finding(
            "N9", "the guard is unreachable", path="bin/otto-workbench", line=None,
            checkbox=True,
        )
        rec = _by_id(review.reconcile.reconcile(
            _prior(n9),
            _ledger(LedgerLine("N9", PriorDisposition.FIXED, "bin/otto-workbench")),
        ), "N9")
        assert rec.ref.path == "bin/otto-workbench"
        assert rec.disposition is PriorDisposition.FIXED

    def test_a_bare_file_carries_forward_by_stable_id(self):
        """Without a path there is no stable ID, and no carry-forward to match."""
        n9 = finding(
            "N9", "the guard is unreachable", path="bin/otto-workbench", line=None,
            checkbox=True,
        )
        rec = _by_id(review.reconcile.reconcile(
            _prior(n9),
            _rev(finding(
                "N3", "the guard is unreachable", path="bin/otto-workbench", line=None,
            )),
        ), "N9")
        assert rec.disposition is PriorDisposition.STILL_OPEN
        assert rec.source is DispositionSource.CARRIED

    def test_ledger_matches_reworded_finding_by_path(self):
        assert review.reconcile.reconcile(
            _prior(F_M1),
            record(
                finding("M4", "db.Query() error is discarded", path="handler.go", line=42),
                ledger=(LedgerLine("M1", PriorDisposition.STILL_OPEN, "handler.go"),),
            ),
        ).unaccounted == []

    def test_ledger_entry_may_carry_a_line_number(self):
        assert review.reconcile.reconcile(
            _prior(F_M1),
            _ledger(LedgerLine("M1", PriorDisposition.FIXED, "handler.go")),
        ).unaccounted == []

    def test_reports_finding_the_review_dropped(self):
        assert review.reconcile.reconcile(
            _prior(F_M1),
            _rev(finding("M1", "unrelated issue", path="other.go", line=7)),
        ).unaccounted == [
            "M1 `handler.go`",
        ]

    def test_one_ledger_entry_does_not_cover_a_sibling_in_the_same_file(self):
        assert review.reconcile.reconcile(
            _prior(
                F_M1,
                finding("M2", "unchecked type assertion", path="handler.go", line=88),
            ),
            _ledger(LedgerLine("M1", PriorDisposition.FIXED, "handler.go")),
        ).unaccounted == ["M2 `handler.go`"]

    def test_reports_only_the_unaccounted_one(self):
        assert review.reconcile.reconcile(
            _prior(F_M1, finding("M2", "stale entry", path="cache.go", line=9)),
            _ledger(LedgerLine("M1", PriorDisposition.FIXED, "handler.go")),
        ).unaccounted == ["M2 `cache.go`"]

    def test_a_declined_verdict_is_recorded_as_stated(self):
        rec = _by_id(review.reconcile.reconcile(
            _prior(F_M1),
            _ledger(LedgerLine(
                "M1", PriorDisposition.DECLINED, "handler.go", "documented tradeoff",
            )),
        ), "M1")
        assert rec.disposition is PriorDisposition.DECLINED
        assert rec.source.stated

    def test_nothing_is_inferred_without_a_worktree(self):
        rec = _by_id(review.reconcile.reconcile(
            _prior(F_M1), _rev(summary="nothing to say."),
        ), "M1")
        assert rec.source is DispositionSource.NONE
        assert rec.reason is UndecidedReason.NOT_CHECKABLE
        assert rec.basis == "there was no worktree to check it against"

    def test_a_decided_finding_carries_no_undecided_reason(self):
        assert _by_id(review.reconcile.reconcile(
            _prior(F_M1),
            _ledger(LedgerLine("M1", PriorDisposition.FIXED, "handler.go")),
        ), "M1").reason is None


# ── What the tree settles on its own ─────────────────────────────────────────


class TestInferredFromTheTree:
    UNDECODABLE_BYTES = b"func handle() \xb2\xb2 leak\n"

    def test_a_deleted_file_fixes_its_findings(self, repo):
        (repo / "gone.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "gone.go").unlink()
        _commit(repo, "after")

        prior = _prior(finding("M1", "leaky handle", path="gone.go", line=4), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is PriorDisposition.FIXED
        assert record.source is DispositionSource.TREE
        assert "no longer in the tree" in record.basis

    def test_a_location_the_parser_could_not_read_is_its_own_reason(self, repo):
        """Not the review's omission — nothing here read the line it wrote."""
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")

        prior = _prior(finding("N1", "the retry loop never terminates", path=""), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "N1")
        assert record.reason is UndecidedReason.NO_LOCATION
        assert record.basis == "it names no file"

    def test_a_file_in_neither_tree_settles_nothing(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")

        prior = _prior(finding("M1", "invented", path="imagined.go", line=4), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is None
        assert record.basis == "`imagined.go` is in neither tree"

    def test_a_file_that_will_not_decode_is_still_read_and_judged(self, repo):
        """Reading the prior blob must not kill the pass, and must still work.

        `_before_text` shells out to `git show <sha>:<path>`, and the pipe
        raised `UnicodeDecodeError` on a file git calls text but UTF-8 cannot
        decode — a second way for one latin-1 file in the diff to destroy a
        review in post-processing, independent of the evidence check.

        The quoted span is ASCII and really is in the prior blob, so reaching
        FIXED requires that blob to have been read *and* decoded. Asserting
        only that the call returned would hold just as well if the read had
        silently come back empty, which is the `out` default on failure and
        would make every finding here undecidable.
        """
        (repo / "latin1.sh").write_bytes(self.UNDECODABLE_BYTES)
        prior_sha = _commit(repo, "before")
        (repo / "latin1.sh").write_bytes(b"rewritten \xb2\xb2 entirely\n")
        _commit(repo, "after")

        prior = _prior(finding("M1", "`func handle()` leaks", path="latin1.sh"), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is PriorDisposition.FIXED
        assert record.source is DispositionSource.TREE

    def test_surviving_code_in_an_undecodable_file_is_not_called_fixed(self, repo):
        """The other half: both reads decode, so the quote is found on both sides."""
        (repo / "latin1.sh").write_bytes(self.UNDECODABLE_BYTES)
        prior_sha = _commit(repo, "before")
        (repo / "latin1.sh").write_bytes(b"func handle() \xb2\xb2 leak still\n")
        _commit(repo, "after")

        prior = _prior(finding("M1", "`func handle()` leaks", path="latin1.sh"), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is None
        assert record.basis == "the code it quotes is still in `latin1.sh`"

    def test_a_missing_file_settles_nothing_without_a_prior_commit(self, repo):
        (repo / "gone.go").write_text(_BEFORE)
        _commit(repo, "before")
        (repo / "gone.go").unlink()
        _commit(repo, "after")

        prior = _prior(finding("M1", "leaky handle", path="gone.go", line=4))
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is None
        assert record.basis == "the prior review names no commit to compare against"

    def test_a_vanished_quotation_fixes_the_finding(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(finding("M1", "`rows, _ := db.Query(sql)` drops the error", path="handler.go", line=4), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is PriorDisposition.FIXED
        assert record.source is DispositionSource.TREE
        assert "`rows, _ := db.Query(sql)` is no longer in `handler.go`" in record.basis

    def test_surviving_quoted_code_settles_nothing(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "unrelated.go").write_text("package main\n")
        _commit(repo, "after")

        prior = _prior(finding("M1", "`defer rows.Close()` runs before the check", path="handler.go", line=5), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is None
        assert record.basis == "the code it quotes is still in `handler.go`"

    def test_a_quotation_the_prior_file_never_held_is_not_evidence(self, repo):
        """A review quotes what it is naming as well as what it is pointing at."""
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(finding("M1", "should call `wrapAndAnnotateError(err)`", path="handler.go", line=4), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is None
        assert record.basis.startswith("nothing it quotes was in `handler.go`")

    def test_a_short_span_is_a_token_rather_than_a_quotation(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(finding("M1", "`_` swallows it", path="handler.go", line=4), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is None

    def test_the_ledger_outranks_the_tree(self, repo):
        """A `Declined` verdict is a judgement no reading of the code overturns."""
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(finding("M1", "`rows, _ := db.Query(sql)` drops the error", path="handler.go", line=4), sha=prior_sha)
        rec = _by_id(review.reconcile.reconcile(prior, _ledger(LedgerLine("M1", PriorDisposition.DECLINED, "handler.go", "the caller checks it")), str(repo)), "M1")
        assert rec.disposition is PriorDisposition.DECLINED
        assert rec.source is DispositionSource.LEDGER

    def test_a_quotation_below_the_finding_line_still_counts(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(finding(
            "M1",
            "the error is dropped\n  The line reads `rows, _ := db.Query(sql)` today.",
            path="handler.go", line=4,
        ), sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(), str(repo)), "M1")
        assert record.disposition is PriorDisposition.FIXED

    def test_the_next_findings_quotation_is_not_borrowed(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        (repo / "cache.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(
            finding("M1", "the close is misplaced", path="cache.go", line=5),
            finding("M2", "`rows, _ := db.Query(sql)` drops the error", path="handler.go", line=4),
            sha=prior_sha,
        )
        reconciliation = review.reconcile.reconcile(prior, _rev(), str(repo))
        assert _by_id(reconciliation, "M1").disposition is None
        assert _by_id(reconciliation, "M2").disposition is PriorDisposition.FIXED

    def test_head_sha_comes_from_the_worktree(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        head_sha = _commit(repo, "after")

        prior = _prior(F_M1, sha=prior_sha, date="2026-08-20")
        reconciliation = review.reconcile.reconcile(prior, _rev(), str(repo))
        assert reconciliation.head_sha == head_sha
        assert reconciliation.prior_date == "2026-08-20"
        assert reconciliation.range_label == f"{prior_sha[:7]} → {head_sha[:7]}"


# ── What is handed back for an agent to settle ───────────────────────────────


class TestPassedOver:
    """The findings a review left out that something can still be done about.

    `unaccounted` answers a neighbouring question and is not a substitute:
    it names every undecided finding, three quarters of which are this
    pipeline's own failures, and it names them rather than reproducing them.
    """

    def _intact(self, repo):
        """A tree that still holds what the prior finding quotes."""
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "README.md").write_text("unrelated\n")
        _commit(repo, "after")
        return _prior(
            finding("M1", "`rows, _ := db.Query(sql)` drops the error", path="handler.go", line=4),
            sha=prior_sha,
        )

    def test_a_finding_the_review_never_mentioned_comes_back_with_its_text(self, repo):
        passed = review.reconcile.passed_over(self._intact(repo), _rev(summary="nothing."), str(repo))
        assert [f.ref.finding_id for f in passed] == ["M1"]
        assert "drops the error" in passed[0].text

    def test_a_finding_the_review_carried_forward_is_settled(self, repo):
        assert review.reconcile.passed_over(
            self._intact(repo),
            _rev(finding(
                "M4", "`rows, _ := db.Query(sql)` drops the error",
                path="handler.go", line=4,
            )),
            str(repo),
        ) == []

    def test_a_finding_the_ledger_ruled_on_is_settled(self, repo):
        assert review.reconcile.passed_over(
            self._intact(repo),
            _ledger(LedgerLine(
                "M1", PriorDisposition.DECLINED, "handler.go", "documented tradeoff",
            )),
            str(repo),
        ) == []

    def test_a_finding_the_tree_says_is_gone_is_settled(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(finding("M1", "`rows, _ := db.Query(sql)` drops the error", path="handler.go", line=4), sha=prior_sha)
        assert review.reconcile.passed_over(prior, _rev(summary="nothing."), str(repo)) == []

    def test_nothing_comes_back_when_there_is_no_tree_to_ask(self):
        """Without a worktree every finding is `NOT_CHECKABLE`, not passed over."""
        assert review.reconcile.passed_over(_prior(F_M1), _rev(summary="nothing.")) == []

    def test_no_prior_review_asks_the_tree_nothing(self, repo):
        assert review.reconcile.passed_over(None, _rev(summary="nothing."), str(repo)) == []

    def test_two_findings_in_one_file_are_matched_apart(self, repo):
        """Their labels differ only by ID, and one is accounted for."""
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "README.md").write_text("unrelated\n")
        _commit(repo, "after")

        prior = _prior(
            finding("M1", "`rows, _ := db.Query(sql)` drops the error", path="handler.go", line=4),
            finding("M2", "`defer rows.Close()` runs on a nil handle", path="handler.go", line=5),
            sha=prior_sha,
        )
        passed = review.reconcile.passed_over(
            prior,
            _ledger(LedgerLine("M1", PriorDisposition.FIXED, "handler.go")),
            str(repo),
        )
        assert [f.ref.finding_id for f in passed] == ["M2"]


# ── The observed case, end to end ────────────────────────────────────────────


class TestRecordPriorFindings:
    """A re-review whose prior findings were all fixed, none of them in the ledger.

    Every one had in fact been fixed by the commit under review, and the run
    warned about all four and kept nothing. What the pipeline holds — the
    worktree and the prior review's own head_sha — is enough to settle them.
    """

    def _observed(self, repo):
        (repo / "worker.py").write_text(
            "def run(cmd):\n"
            "    result = subprocess.run(cmd, capture_output=True)\n"
            "    return result\n"
        )
        (repo / "docs.py").write_text("def helper():\n    return 1\n")
        prior_sha = _commit(repo, "before")
        (repo / "worker.py").write_text(
            "def run(cmd):\n"
            "    result = subprocess.run(cmd, capture_output=True, check=True)\n"
            "    return result\n"
        )
        (repo / "docs.py").write_text('def helper():\n    """Now documented."""\n    return 1\n')
        _commit(repo, "after")
        return prior_sha

    def _prior_text(self, prior_sha):
        return _prior(
            finding(
                "S1",
                "`subprocess.run(cmd, capture_output=True)` never checks the exit code",
                path="worker.py", line=2,
            ),
            finding("S2", "`def helper():` carries no docstring", path="docs.py"),
            sha=prior_sha,
            date="2026-08-20",
        )

    def test_fixed_findings_absent_from_the_ledger_are_not_warned_about(
        self, repo, capsys,
    ):
        prior_sha = self._observed(repo)
        review_path = repo / "review.md"
        review_path.write_text("## Summary\nThe change looks good.\n")

        reconciliation = review.reconcile.record_prior_findings(
            str(review_path), self._prior_text(prior_sha),
            _rev(summary="The change looks good."), str(repo),
        )
        record = _by_id(reconciliation, "S1")
        assert record.disposition is PriorDisposition.FIXED
        assert record.source is DispositionSource.TREE
        assert "S1" not in capsys.readouterr().err

    def test_what_the_tree_cannot_settle_is_still_reported(self, repo, capsys):
        prior_sha = self._observed(repo)
        review_path = repo / "review.md"
        review_path.write_text("## Summary\nThe change looks good.\n")

        review.reconcile.record_prior_findings(
            str(review_path), self._prior_text(prior_sha), _rev(summary="The change looks good."), str(repo),
        )
        err = capsys.readouterr().err
        assert "1 of 2 prior findings undecided" in err
        assert "S2 `docs.py`" in err
        assert "the code it quotes is still in `docs.py`" in err

    def test_the_warning_names_what_was_checked_and_the_counts(self, repo, capsys):
        prior_sha = self._observed(repo)
        review_path = repo / "review.md"
        review_path.write_text("## Summary\nThe change looks good.\n")

        review.reconcile.record_prior_findings(
            str(review_path), self._prior_text(prior_sha), _rev(summary="The change looks good."), str(repo),
        )
        err = capsys.readouterr().err
        assert f"{prior_sha[:7]} → " in err
        assert "1 Fixed, 1 Undecided, 1 inferred from the tree" in err

    def test_the_record_outlives_the_run(self, repo):
        prior_sha = self._observed(repo)
        review_path = repo / "review.md"
        review_path.write_text("## Summary\nThe change looks good.\n")

        review.reconcile.record_prior_findings(
            str(review_path), self._prior_text(prior_sha), _rev(summary="The change looks good."), str(repo),
        )
        sidecar = json.loads((repo / FILENAME_PRIOR_FINDINGS).read_text())
        assert sidecar["prior_sha"] == prior_sha
        assert sidecar["prior_date"] == "2026-08-20"
        by_id = {r["ref"]["finding_id"]: r for r in sidecar["records"]}
        assert by_id["S1"]["disposition"] == PriorDisposition.FIXED.value
        assert by_id["S1"]["source"] == DispositionSource.TREE.value
        assert by_id["S1"]["reason"] is None
        assert by_id["S2"]["disposition"] is None
        assert by_id["S2"]["source"] == DispositionSource.NONE.value
        assert by_id["S2"]["reason"] == UndecidedReason.NOT_MENTIONED.value

    def test_no_prior_review_records_nothing(self, repo):
        review_path = repo / "review.md"
        review_path.write_text("## Summary\nFirst review.\n")
        assert review.reconcile.record_prior_findings(str(review_path), None, _rev(summary="First review."), str(repo)) is None
        assert not (repo / FILENAME_PRIOR_FINDINGS).exists()

    def test_a_prior_review_with_no_findings_records_nothing(self, repo):
        review_path = repo / "review.md"
        review_path.write_text("## Summary\nStill clean.\n")
        prior = _prior(summary="Nothing to report.", sha="a" * 40)
        assert review.reconcile.record_prior_findings(str(review_path), prior, _rev(summary="Still clean."), str(repo)) is None
        assert not (repo / FILENAME_PRIOR_FINDINGS).exists()

    def test_a_fully_accounted_ledger_reports_without_warning(self, repo, capsys):
        prior_sha = self._observed(repo)
        review_path = repo / "review.md"
        review_path.write_text("## Summary\nBoth prior findings addressed.\n")
        review.reconcile.record_prior_findings(
            str(review_path), self._prior_text(prior_sha),
            record(
                summary="Both prior findings addressed.",
                ledger=(
                    LedgerLine("S1", PriorDisposition.FIXED, "worker.py"),
                    LedgerLine("S2", PriorDisposition.FIXED, "docs.py"),
                ),
            ), str(repo),
        )
        err = capsys.readouterr().err
        assert "undecided" not in err
        assert "Reconciled 2 prior findings" in err


def test_a_prior_finding_is_referenced_by_its_own_path():
    record = ReviewRecord(findings=(RecordFinding(id="M1", severity="M", path="a.py", line=1, body="x"),))
    assert _prior_findings(record)[0].ref == FindingRef("M1", "a.py")


def test_the_typed_ledger_settles_a_prior_finding():
    prior = StoredReview(ReviewRecord(findings=(
        RecordFinding(id="M1", severity="M", path="a.py", line=1, body="x"),
    )), ReviewMeta())
    review = ReviewRecord(ledger=(LedgerLine("M1", PriorDisposition.DECLINED, "a.py"),))
    (rec,) = reconcile(prior, review).records
    assert (rec.disposition, rec.source) == (PriorDisposition.DECLINED, DispositionSource.LEDGER)


def test_an_unreadable_ledger_line_does_not_settle_from_the_ledger():
    rec = _by_id(
        reconcile(_prior(F_M1), _rev()),
        "M1",
    )
    assert rec.disposition is None
    assert rec.source is not DispositionSource.LEDGER


def test_no_undecided_reason_is_about_an_unreadable_verdict():
    assert "unreadable verdict" not in {r.value for r in UndecidedReason}
