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
from review.ingest import ingest_review
from review.store import StoredReview
from review.types import ReviewMeta


def _rev(text: str):
    return ingest_review(text, strict=False)

from review.paths import FILENAME_PRIOR_FINDINGS
from review.grammar import FindingIdentity
from review.reconcile import DispositionSource, UndecidedReason, _prior_findings, reconcile
from review.record import LedgerLine, RecordFinding, ReviewRecord
from review.types import FindingRef, PriorDisposition

PRIOR_ONE_FINDING = (
    "## Must fix\n"
    "- **[M1]** **`handler.go:42`** — missing error check\n"
)

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


def _prior(body: str, sha: str = "", date: str = "") -> StoredReview:
    """A prior round as data: the record plus the sidecar facts reconciliation reads."""
    text = body if body.lstrip().startswith("#") else f"## Must fix\n{body}"
    return StoredReview(
        ingest_review(text, strict=False),
        ReviewMeta(head_sha=sha, date=date),
    )


def _ledger(*lines: str) -> str:
    return f"## {SECTION_PRIOR_FINDINGS}\n" + "".join(f"{line}\n" for line in lines)


def _by_id(reconciliation, finding_id: str):
    return next(r for r in reconciliation.records if r.ref.finding_id == finding_id)


# ── Reading the prior review's findings ──────────────────────────────────────


class TestParsePriorFindings:
    """How `_prior_findings` reads a prior record before reconciling it."""

    def test_a_findings_quotation_below_its_first_line_travels_with_it(self):
        prior = (
            PRIOR_ONE_FINDING
            + "  The call reads `rows, _ := db.Query(sql)` today.\n"
            + "- **[M2]** **`cache.go:9`** — stale entry\n"
        )
        first, second = review.reconcile._prior_findings(_prior(prior).record)
        assert "rows, _ := db.Query(sql)" in first.text
        assert "rows, _ := db.Query(sql)" not in second.text

    def test_a_findings_text_stops_at_the_next_section(self):
        prior = PRIOR_ONE_FINDING + "\n## Verdict\nRequest changes.\n"
        first = review.reconcile._prior_findings(_prior(prior).record)[0]
        assert "Request changes" not in first.text


# ── What the review itself accounts for ──────────────────────────────────────


class TestAccountedByTheReview:
    def test_empty_prior_reconciles_nothing(self):
        assert review.reconcile.reconcile(_prior(""), _rev("<!-- sid:abc -->")).records == []

    def test_sid_marker_carries_finding_forward(self):
        sid = FindingIdentity("handler.go", None, "missing error check").stable_id
        review_md = (
            "## Must fix\n"
            f"- **[M1]** <!-- sid:{sid} --> **`other.go:1`** — reworded\n"
        )
        record = _by_id(review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_md)), "M1")
        assert record.disposition is PriorDisposition.STILL_OPEN
        assert record.source is DispositionSource.CARRIED

    def test_verbatim_carry_forward_without_marker(self):
        review_md = (
            "## Must fix\n"
            "- **[M2]** **`handler.go:42`** — missing error check\n"
        )
        assert review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_md)).unaccounted == []

    def test_ledger_entry_accounts_for_fixed_finding(self):
        review_path = "## Summary\nAll prior findings addressed.\n" + _ledger(
            "- **[M1]** `handler.go` — Fixed",
        )
        record = _by_id(review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_path)), "M1")
        assert record.disposition is PriorDisposition.FIXED
        assert record.source is DispositionSource.LEDGER

    def test_a_verdict_ending_a_sentence_is_read_as_that_verdict(self):
        """The ledger form a synthesis agent writes when its detail is prose."""
        review_md = _ledger("- **[M1]** `handler.go` — Fixed. The error is checked now.")
        record = _by_id(review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_md)), "M1")
        assert record.disposition is PriorDisposition.FIXED
        assert record.source is DispositionSource.LEDGER

    def test_a_prior_finding_citing_a_bare_file_is_still_matched(self):
        """A location a review wrote without a line number still names its file."""
        prior = "## Nit\n- [ ] **[N9]** `bin/otto-workbench` — the guard is unreachable\n"
        review_md = _ledger("- **[N9]** `bin/otto-workbench` — Fixed")
        record = _by_id(review.reconcile.reconcile(_prior(prior), _rev(review_md)), "N9")
        assert record.ref.path == "bin/otto-workbench"
        assert record.disposition is PriorDisposition.FIXED

    def test_a_bare_file_carries_forward_by_stable_id(self):
        """Without a path there is no stable ID, and no carry-forward to match."""
        prior = "## Nit\n- [ ] **[N9]** `bin/otto-workbench` — the guard is unreachable\n"
        review_md = "## Nit\n- **[N3]** `bin/otto-workbench` — the guard is unreachable\n"
        record = _by_id(review.reconcile.reconcile(_prior(prior), _rev(review_md)), "N9")
        assert record.disposition is PriorDisposition.STILL_OPEN
        assert record.source is DispositionSource.CARRIED

    def test_ledger_matches_reworded_finding_by_path(self):
        review_path = (
            "## Must fix\n"
            "- **[M4]** **`handler.go:42`** — db.Query() error is discarded\n"
            + _ledger("- **[M1]** `handler.go` — Still open")
        )
        assert review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_path)).unaccounted == []

    def test_ledger_entry_may_carry_a_line_number(self):
        review_md = _ledger("- **[M1]** `handler.go:42` — Fixed")
        assert review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_md)).unaccounted == []

    def test_reports_finding_the_review_dropped(self):
        review_md = "## Must fix\n- **[M1]** **`other.go:7`** — unrelated issue\n"
        assert review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_md)).unaccounted == [
            "M1 `handler.go`",
        ]

    def test_one_ledger_entry_does_not_cover_a_sibling_in_the_same_file(self):
        prior = (
            PRIOR_ONE_FINDING
            + "- **[M2]** **`handler.go:88`** — unchecked type assertion\n"
        )
        review_md = _ledger("- **[M1]** `handler.go` — Fixed")
        assert review.reconcile.reconcile(_prior(prior), _rev(review_md)).unaccounted == ["M2 `handler.go`"]

    def test_reports_only_the_unaccounted_one(self):
        prior = PRIOR_ONE_FINDING + "- **[M2]** **`cache.go:9`** — stale entry\n"
        review_md = _ledger("- **[M1]** `handler.go` — Fixed")
        assert review.reconcile.reconcile(_prior(prior), _rev(review_md)).unaccounted == ["M2 `cache.go`"]

    def test_a_declined_verdict_is_recorded_as_stated(self):
        review_md = _ledger("- **[M1]** `handler.go` — Declined — documented tradeoff")
        record = _by_id(review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_md)), "M1")
        assert record.disposition is PriorDisposition.DECLINED
        assert record.source.stated

    def test_nothing_is_inferred_without_a_worktree(self):
        review_md = "## Summary\nnothing to say.\n"
        record = _by_id(review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_md)), "M1")
        assert record.source is DispositionSource.NONE
        assert record.reason is UndecidedReason.NOT_CHECKABLE
        assert record.basis == "there was no worktree to check it against"

    def test_a_decided_finding_carries_no_undecided_reason(self):
        review_md = _ledger("- **[M1]** `handler.go` — Fixed")
        assert _by_id(review.reconcile.reconcile(_prior(PRIOR_ONE_FINDING), _rev(review_md)), "M1").reason is None


# ── What the tree settles on its own ─────────────────────────────────────────


class TestInferredFromTheTree:
    UNDECODABLE_BYTES = b"func handle() \xb2\xb2 leak\n"

    def test_a_deleted_file_fixes_its_findings(self, repo):
        (repo / "gone.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "gone.go").unlink()
        _commit(repo, "after")

        prior = _prior("- **[M1]** **`gone.go:4`** — leaky handle\n", sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
        assert record.disposition is PriorDisposition.FIXED
        assert record.source is DispositionSource.TREE
        assert "no longer in the tree" in record.basis

    def test_a_location_the_parser_could_not_read_is_its_own_reason(self, repo):
        """Not the review's omission — nothing here read the line it wrote."""
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")

        prior = _prior("- **[N1]** the retry loop never terminates\n", sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "N1")
        assert record.reason is UndecidedReason.NO_LOCATION
        assert record.basis == "it names no file"

    def test_a_file_in_neither_tree_settles_nothing(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")

        prior = _prior("- **[M1]** **`imagined.go:4`** — invented\n", sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
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

        prior = _prior(
            "- **[M1]** **`latin1.sh:1`** — `func handle()` leaks\n", sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
        assert record.disposition is PriorDisposition.FIXED
        assert record.source is DispositionSource.TREE

    def test_surviving_code_in_an_undecodable_file_is_not_called_fixed(self, repo):
        """The other half: both reads decode, so the quote is found on both sides."""
        (repo / "latin1.sh").write_bytes(self.UNDECODABLE_BYTES)
        prior_sha = _commit(repo, "before")
        (repo / "latin1.sh").write_bytes(b"func handle() \xb2\xb2 leak still\n")
        _commit(repo, "after")

        prior = _prior(
            "- **[M1]** **`latin1.sh:1`** — `func handle()` leaks\n", sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
        assert record.disposition is None
        assert record.basis == "the code it quotes is still in `latin1.sh`"

    def test_a_missing_file_settles_nothing_without_a_prior_commit(self, repo):
        (repo / "gone.go").write_text(_BEFORE)
        _commit(repo, "before")
        (repo / "gone.go").unlink()
        _commit(repo, "after")

        prior = _prior("- **[M1]** **`gone.go:4`** — leaky handle\n")
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
        assert record.disposition is None
        assert record.basis == "the prior review names no commit to compare against"

    def test_a_vanished_quotation_fixes_the_finding(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(
            "- **[M1]** **`handler.go:4`** — `rows, _ := db.Query(sql)` drops the error\n",
            sha=prior_sha,
        )
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
        assert record.disposition is PriorDisposition.FIXED
        assert record.source is DispositionSource.TREE
        assert "`rows, _ := db.Query(sql)` is no longer in `handler.go`" in record.basis

    def test_surviving_quoted_code_settles_nothing(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "unrelated.go").write_text("package main\n")
        _commit(repo, "after")

        prior = _prior(
            "- **[M1]** **`handler.go:5`** — `defer rows.Close()` runs before the check\n",
            sha=prior_sha,
        )
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
        assert record.disposition is None
        assert record.basis == "the code it quotes is still in `handler.go`"

    def test_a_quotation_the_prior_file_never_held_is_not_evidence(self, repo):
        """A review quotes what it is naming as well as what it is pointing at."""
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(
            "- **[M1]** **`handler.go:4`** — should call `wrapAndAnnotateError(err)`\n",
            sha=prior_sha,
        )
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
        assert record.disposition is None
        assert record.basis.startswith("nothing it quotes was in `handler.go`")

    def test_a_short_span_is_a_token_rather_than_a_quotation(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior("- **[M1]** **`handler.go:4`** — `_` swallows it\n", sha=prior_sha)
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
        assert record.disposition is None

    def test_the_ledger_outranks_the_tree(self, repo):
        """A `Declined` verdict is a judgement no reading of the code overturns."""
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(
            "- **[M1]** **`handler.go:4`** — `rows, _ := db.Query(sql)` drops the error\n",
            sha=prior_sha,
        )
        review_md = _ledger("- **[M1]** `handler.go` — Declined — the caller checks it")
        record = _by_id(review.reconcile.reconcile(prior, _rev(review_md), str(repo)), "M1")
        assert record.disposition is PriorDisposition.DECLINED
        assert record.source is DispositionSource.LEDGER

    def test_a_quotation_below_the_finding_line_still_counts(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(
            "- **[M1]** **`handler.go:4`** — the error is dropped\n"
            "  The line reads `rows, _ := db.Query(sql)` today.\n",
            sha=prior_sha,
        )
        record = _by_id(review.reconcile.reconcile(prior, _rev(""), str(repo)), "M1")
        assert record.disposition is PriorDisposition.FIXED

    def test_the_next_findings_quotation_is_not_borrowed(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        (repo / "cache.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(
            "- **[M1]** **`cache.go:5`** — the close is misplaced\n"
            "- **[M2]** **`handler.go:4`** — `rows, _ := db.Query(sql)` drops the error\n",
            sha=prior_sha,
        )
        reconciliation = review.reconcile.reconcile(prior, _rev(""), str(repo))
        assert _by_id(reconciliation, "M1").disposition is None
        assert _by_id(reconciliation, "M2").disposition is PriorDisposition.FIXED

    def test_head_sha_comes_from_the_worktree(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        head_sha = _commit(repo, "after")

        prior = _prior(PRIOR_ONE_FINDING, sha=prior_sha, date="2026-08-20")
        reconciliation = review.reconcile.reconcile(prior, _rev(""), str(repo))
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
            "- **[M1]** **`handler.go:4`** — `rows, _ := db.Query(sql)` drops the error\n",
            sha=prior_sha,
        )

    def test_a_finding_the_review_never_mentioned_comes_back_with_its_text(self, repo):
        passed = review.reconcile.passed_over(self._intact(repo), _rev("## Summary\nnothing.\n"), str(repo))
        assert [f.ref.finding_id for f in passed] == ["M1"]
        assert "drops the error" in passed[0].text

    def test_a_finding_the_review_carried_forward_is_settled(self, repo):
        review_md = (
            "## Must fix\n"
            "- **[M4]** **`handler.go:4`** — `rows, _ := db.Query(sql)` drops the error\n"
        )
        assert review.reconcile.passed_over(self._intact(repo), _rev(review_md), str(repo)) == []

    def test_a_finding_the_ledger_ruled_on_is_settled(self, repo):
        review_md = _ledger("- **[M1]** `handler.go` — Declined — documented tradeoff")
        assert review.reconcile.passed_over(self._intact(repo), _rev(review_md), str(repo)) == []

    def test_a_finding_the_tree_says_is_gone_is_settled(self, repo):
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "handler.go").write_text(_AFTER)
        _commit(repo, "after")

        prior = _prior(
            "- **[M1]** **`handler.go:4`** — `rows, _ := db.Query(sql)` drops the error\n",
            sha=prior_sha,
        )
        assert review.reconcile.passed_over(prior, _rev("## Summary\nnothing.\n"), str(repo)) == []

    def test_nothing_comes_back_when_there_is_no_tree_to_ask(self):
        """Without a worktree every finding is `NOT_CHECKABLE`, not passed over."""
        assert review.reconcile.passed_over(_prior(PRIOR_ONE_FINDING), _rev("## Summary\nnothing.\n")) == []

    def test_no_prior_review_asks_the_tree_nothing(self, repo):
        assert review.reconcile.passed_over(None, _rev("## Summary\nnothing.\n"), str(repo)) == []

    def test_two_findings_in_one_file_are_matched_apart(self, repo):
        """Their labels differ only by ID, and one is accounted for."""
        (repo / "handler.go").write_text(_BEFORE)
        prior_sha = _commit(repo, "before")
        (repo / "README.md").write_text("unrelated\n")
        _commit(repo, "after")

        prior = _prior(
            "- **[M1]** **`handler.go:4`** — `rows, _ := db.Query(sql)` drops the error\n"
            "- **[M2]** **`handler.go:5`** — `defer rows.Close()` runs on a nil handle\n",
            sha=prior_sha,
        )
        review_md = _ledger("- **[M1]** `handler.go:4` — Fixed")
        passed = review.reconcile.passed_over(prior, _rev(review_md), str(repo))
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
            "## Should fix\n"
            "- **[S1]** **`worker.py:2`** — `subprocess.run(cmd, capture_output=True)` "
            "never checks the exit code\n"
            "- **[S2]** **`docs.py:1`** — `def helper():` carries no docstring\n",
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
            str(review_path), self._prior_text(prior_sha), _rev(review_path.read_text()), str(repo),
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
            str(review_path), self._prior_text(prior_sha), _rev(review_path.read_text()), str(repo),
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
            str(review_path), self._prior_text(prior_sha), _rev(review_path.read_text()), str(repo),
        )
        err = capsys.readouterr().err
        assert f"{prior_sha[:7]} → " in err
        assert "1 Fixed, 1 Undecided, 1 inferred from the tree" in err

    def test_the_record_outlives_the_run(self, repo):
        prior_sha = self._observed(repo)
        review_path = repo / "review.md"
        review_path.write_text("## Summary\nThe change looks good.\n")

        review.reconcile.record_prior_findings(
            str(review_path), self._prior_text(prior_sha), _rev(review_path.read_text()), str(repo),
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
        assert review.reconcile.record_prior_findings(str(review_path), None, _rev(review_path.read_text()), str(repo)) is None
        assert not (repo / FILENAME_PRIOR_FINDINGS).exists()

    def test_a_prior_review_with_no_findings_records_nothing(self, repo):
        review_path = repo / "review.md"
        review_path.write_text("## Summary\nStill clean.\n")
        prior = _prior("## Summary\nNothing to report.\n", sha="a" * 40)
        assert review.reconcile.record_prior_findings(str(review_path), prior, _rev(review_path.read_text()), str(repo)) is None
        assert not (repo / FILENAME_PRIOR_FINDINGS).exists()

    def test_a_fully_accounted_ledger_reports_without_warning(self, repo, capsys):
        prior_sha = self._observed(repo)
        review_path = repo / "review.md"
        review_path.write_text(
            "## Summary\nBoth prior findings addressed.\n"
            + _ledger(
                "- **[S1]** `worker.py` — Fixed",
                "- **[S2]** `docs.py` — Fixed",
            )
        )
        review.reconcile.record_prior_findings(
            str(review_path), self._prior_text(prior_sha), _rev(review_path.read_text()), str(repo),
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


def test_no_undecided_reason_is_about_an_unreadable_verdict():
    assert "unreadable verdict" not in {r.value for r in UndecidedReason}
