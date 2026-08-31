import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from review_grammar import FindingIdentity  # noqa: E402

DESC = "missing error check"


class TestFindingIdentityReadsEveryLocationShape:
    """Every shape a review writes for a location yields an identity.

    Before this module existed, dedup read the path with a bold-only regex and
    returned nothing for the three non-bold shapes, while carry-forward hashed
    all of them to the same stable ID. The same finding shipped twice.
    """

    def test_bold_backtick_path_with_line(self):
        ident = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}")
        assert ident.path == "ai/lib/x.py"
        assert ident.line == 12

    def test_bold_backtick_path_without_line(self):
        ident = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py`** — {DESC}")
        assert ident.path == "ai/lib/x.py"
        assert ident.line is None

    def test_plain_backtick_path_is_no_longer_invisible(self):
        ident = FindingIdentity.of(f"- **[M1]** `ai/lib/x.py` — {DESC}")
        assert ident is not None
        assert ident.path == "ai/lib/x.py"

    def test_plain_backtick_path_with_line_is_no_longer_invisible(self):
        ident = FindingIdentity.of(f"- **[M1]** `ai/lib/x.py:12` — {DESC}")
        assert ident is not None
        assert ident.path == "ai/lib/x.py"
        assert ident.line == 12

    def test_bare_path_is_no_longer_invisible(self):
        ident = FindingIdentity.of(f"- **[M1]** ai/lib/x.py:12 — {DESC}")
        assert ident is not None
        assert ident.path == "ai/lib/x.py"

    def test_bold_label_that_is_not_a_filename_still_reads(self):
        ident = FindingIdentity.of(f"- **[M1]** **Documentation** — {DESC}")
        assert ident.path == "Documentation"


class TestDedupStaysLineSensitive:
    """Two findings at different lines of one file are two findings.

    The stable ID is line-insensitive on purpose, so a finding carries forward
    when the code moves. The dedup key is not, and unifying the two readers
    must not quietly make it so.
    """

    def test_same_file_different_lines_do_not_collapse(self):
        a = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}")
        b = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:40`** — {DESC}")
        assert a.dedup_key != b.dedup_key

    def test_same_file_same_line_collapse(self):
        a = FindingIdentity.of(f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}")
        b = FindingIdentity.of(f"- **[M1]** `ai/lib/x.py:12` — {DESC}")
        assert a.dedup_key == b.dedup_key


class TestStableIdIsUnchanged:
    """The markers are on disk; the formula cannot move.

    Every prior review carries `<!-- sid:xxxx -->` markers this formula
    produced. A different hash makes one round of re-reviews report every
    prior finding as new.
    """

    def test_every_shape_of_one_finding_hashes_the_same(self):
        shapes = [
            f"- **[M1]** **`ai/lib/x.py:12`** — {DESC}",
            f"- **[M1]** **`ai/lib/x.py`** — {DESC}",
            f"- **[M1]** `ai/lib/x.py` — {DESC}",
            f"- **[M1]** `ai/lib/x.py:12` — {DESC}",
        ]
        ids = {FindingIdentity.of(s).stable_id for s in shapes}
        assert ids == {"0e9022af"}
