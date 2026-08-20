"""Tests for the reviews listing `pr review --list` serves.

The row schema is a contract another repo reads, so these assert the field set
and the vocabulary as facts, not as whatever the dataclass currently holds.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review_common  # noqa: E402
import review_listing  # noqa: E402

# `reviews_dir` is not imported — pytest discovers conftest fixtures itself,
# and importing one shadows the fixture with a plain function.
from conftest import seed_review  # noqa: E402

_REVIEW_MD = """## Must fix
- **[M1]** a.py:1 — bug

## Should fix
- **[S1]** b.py:2 — smell

## Nits
- **[N1]** c.py:3 — spacing
"""


def _review(reviews_dir, name="widget-42", body=_REVIEW_MD, **meta):
    """One finding per severity, which is what the vocabulary tests read."""
    return seed_review(reviews_dir, name=name, body=body, **meta)


# ── rows ────────────────────────────────────────────────────────────────────


def test_a_row_reports_what_the_sidecar_attributes_it_to(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=42, head_sha="abc123",
            head_ref="isaac/feat/x", base_ref="main", review_type="full",
            mode="pr", started_at="2026-08-18T13:47:03+00:00",
            reviewed_at="2026-08-18T14:02:11+00:00")

    row, = review_listing.rows()

    assert row.repo == "acme/widget"
    assert row.pr_number == 42
    assert row.head_sha == "abc123"
    assert row.head_ref == "isaac/feat/x"
    assert row.base_ref == "main"
    assert row.review_type == "full"
    assert row.mode == "pr"
    assert row.started_at == "2026-08-18T13:47:03+00:00"
    assert row.reviewed_at == "2026-08-18T14:02:11+00:00"


def test_a_row_is_attributed_by_the_sidecar_not_the_directory_name(reviews_dir):
    """The name is chosen from a repo's short name, which two repos can share."""
    _review(reviews_dir, name="widget-42", repo="other-org/widget", pr_number=7)

    row, = review_listing.rows()

    assert row.repo == "other-org/widget"
    assert row.pr_number == 7


def test_a_review_predating_the_sidecar_is_still_listed(reviews_dir):
    """Unattributed is a fact about that review; dropping it would hide one the
    consumer can still open."""
    _review(reviews_dir)

    row, = review_listing.rows()

    assert row.repo == ""
    assert row.pr_number is None
    assert row.review_file.endswith("/review.md")
    assert row.reviewed_at, "the deliverable's mtime is the only date left"


def test_findings_are_keyed_by_the_codebase_severity_vocabulary(reviews_dir):
    """Keyed off `SeverityConfig.json_key` rather than fields of its own, so a
    fifth severity cannot leave the listing describing four."""
    _review(reviews_dir)

    row, = review_listing.rows()

    expected = {s.json_key for s in review_common.SEVERITIES} | {"total"}
    assert set(row.findings) == expected
    assert row.findings["must_fix"] == 1
    assert row.findings["should_fix"] == 1
    assert row.findings["nit"] == 1
    assert row.findings["total"] == 3


def test_a_row_carries_the_path_and_never_the_content(reviews_dir):
    """A consumer polling on an interval would otherwise carry every review's
    full text on every tick."""
    review_dir = _review(reviews_dir)

    row, = review_listing.rows()

    assert row.review_file == str(review_dir / "review.md")
    assert "review_content" not in review_listing.document(1)["reviews"][0]


def test_a_self_review_reports_no_verdict(reviews_dir):
    """A self-review is advisory — it has no PR to approve or block. The row
    has to hand `resolve_review_verdict` the mode for that to hold; letting it
    default would publish a blocking verdict for a review of nothing."""
    _review(reviews_dir, repo="acme/widget", pr_number=1, mode="self")

    row, = review_listing.rows()

    assert row.findings["must_fix"] == 1, "the same findings a PR review has"
    assert row.verdict == ""


def test_a_pr_review_of_the_same_findings_does_report_one(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=1, mode="pr")

    row, = review_listing.rows()

    assert row.verdict == review_common.ReviewVerdict.CHANGES_REQUESTED.value


def test_an_orphaned_directory_is_not_a_review(reviews_dir):
    """A run that produced nothing is `pr gc`'s business — a consumer asking
    what has been reviewed is not asking about it."""
    (reviews_dir / "widget-99").mkdir()
    (reviews_dir / "widget-99" / "meta.json").write_text('{"repo": "acme/widget"}')

    assert review_listing.rows() == []


def test_a_stray_file_is_not_a_review(reviews_dir):
    (reviews_dir / "check_hunks.py").write_text("# agent scratch\n")

    assert review_listing.rows() == []


def test_a_missing_reviews_root_yields_no_rows(tmp_path, monkeypatch):
    """A machine that has never run a review is not an error."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "never-written"))

    assert review_listing.rows() == []


# ── document ────────────────────────────────────────────────────────────────


def test_the_document_echoes_the_version_the_caller_declared(reviews_dir):
    """Echoed rather than restated from a constant, so a document can never
    claim a version its reader did not ask for."""
    _review(reviews_dir, repo="acme/widget", pr_number=42)

    for version in review_listing.SCHEMA_VERSIONS:
        assert review_listing.document(version)["schema_version"] == version


def test_the_document_serialises_every_field_of_a_row(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=42)

    row, = review_listing.document(1)["reviews"]

    assert set(row) == set(review_listing.ReviewRow.__dataclass_fields__)
    assert json.loads(json.dumps(row)) == row, "a row has to survive the wire"


def test_version_one_is_served(reviews_dir):
    """The supported set is allowed to shrink, and dropping 1 is the breaking
    change that has to be a deliberate edit here."""
    assert 1 in review_listing.SCHEMA_VERSIONS


# ── table ───────────────────────────────────────────────────────────────────


def test_the_table_says_so_when_there_is_nothing_to_show():
    assert review_listing.render_table([]) == ["No reviews."]


def test_the_table_names_an_unattributed_review(reviews_dir):
    _review(reviews_dir)

    header, row = review_listing.render_table(review_listing.rows())

    assert header.split() == ["REPO", "PR", "VERDICT", "FINDINGS", "REVIEWED"]
    assert row.startswith("(unattributed)")


def test_the_table_reports_the_pr_and_the_finding_total(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=42,
            reviewed_at="2026-08-18T14:02:11+00:00")

    _, row = review_listing.render_table(review_listing.rows())

    assert row.split() == [
        "acme/widget", "#42", "changes_requested", "3", "2026-08-18T14:02:11+00:00",
    ]
