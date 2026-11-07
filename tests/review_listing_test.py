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

import pr.domains  # noqa: E402
import review.listing  # noqa: E402
import review.types  # noqa: E402

# `reviews_dir` is not imported — pytest discovers conftest fixtures itself,
# and importing one shadows the fixture with a plain function.
from review_record_support import finding, record, seed_record  # noqa: E402

_REVIEW = record(
    finding("M1", "a.py:1 — bug", path=""),
    finding("S1", "b.py:2 — smell", path=""),
    finding("N1", "c.py:3 — spacing", path=""),
)


def _review(reviews_dir, name="widget-42", rec=_REVIEW, **meta):
    """One finding per severity, which is what the vocabulary tests read."""
    return seed_record(reviews_dir, name=name, rec=rec, **meta)


# ── rows ────────────────────────────────────────────────────────────────────


def test_a_row_reports_what_the_sidecar_attributes_it_to(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=42, head_sha="abc123",
            head_ref="isaac/feat/x", base_ref="main", review_type="full",
            mode="pr", started_at="2026-08-18T13:47:03+00:00",
            reviewed_at="2026-08-18T14:02:11+00:00")

    row, = review.listing.rows()

    assert row.repo == "acme/widget"
    assert row.pr_number == 42
    assert row.head_sha == "abc123"
    assert row.reviewed_through == "abc123"
    assert row.head_ref == "isaac/feat/x"
    assert row.base_ref == "main"
    assert row.review_type == "full"
    assert row.mode == "pr"
    assert row.started_at == "2026-08-18T13:47:03+00:00"
    assert row.reviewed_at == "2026-08-18T14:02:11+00:00"


def test_a_row_reports_reviewed_through_from_covered_sha(reviews_dir):
    """Currency is covered_sha when set, not the original head."""
    _review(reviews_dir, repo="acme/widget", pr_number=42, head_sha="abc123",
            covered_sha="def456")

    row, = review.listing.rows()

    assert row.head_sha == "abc123"
    assert row.reviewed_through == "def456"


def test_a_row_is_attributed_by_the_sidecar_not_the_directory_name(reviews_dir):
    """The name is chosen from a repo's short name, which two repos can share."""
    _review(reviews_dir, name="widget-42", repo="other-org/widget", pr_number=7)

    row, = review.listing.rows()

    assert row.repo == "other-org/widget"
    assert row.pr_number == 7


def test_a_review_predating_the_sidecar_is_still_listed(reviews_dir):
    """Unattributed is a fact about that review; dropping it would hide one the
    consumer can still open."""
    d = _review(reviews_dir)
    (d / "meta.json").unlink()

    row, = review.listing.rows()

    assert row.repo == ""
    assert row.pr_number is None
    assert row.review_file.endswith("/review.md")
    assert row.reviewed_at, "the deliverable's mtime is the only date left"


def test_findings_are_keyed_by_the_codebase_severity_vocabulary(reviews_dir):
    """Keyed off `SeverityConfig.json_key` rather than fields of its own, so a
    fifth severity cannot leave the listing describing four."""
    _review(reviews_dir)

    row, = review.listing.rows()

    expected = {s.json_key for s in review.types.SEVERITIES} | {"total"}
    assert set(row.findings) == expected
    assert row.findings["must_fix"] == 1
    assert row.findings["should_fix"] == 1
    assert row.findings["nit"] == 1
    assert row.findings["total"] == 3


def test_a_row_carries_the_path_and_never_the_content(reviews_dir):
    """A consumer polling on an interval would otherwise carry every review's
    full text on every tick."""
    review_dir = _review(reviews_dir)

    row, = review.listing.rows()

    assert row.review_file == str(review_dir / "review.md")
    assert "review_content" not in review.listing.document(1)["reviews"][0]


def test_a_self_review_reports_no_verdict(reviews_dir):
    """A self-review is advisory — it has no PR to approve or block. The row
    has to hand `resolve_review_verdict` the mode for that to hold; letting it
    default would publish a blocking verdict for a review of nothing."""
    _review(reviews_dir, repo="acme/widget", pr_number=1, mode="self")

    row, = review.listing.rows()

    assert row.findings["must_fix"] == 1, "the same findings a PR review has"
    assert row.verdict == ""


def test_a_pr_review_of_the_same_findings_does_report_one(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=1, mode="pr")

    row, = review.listing.rows()

    assert row.verdict == pr.domains.ReviewVerdict.CHANGES_REQUESTED.value


def test_an_orphaned_directory_is_not_a_review(reviews_dir):
    """A run that produced nothing is `pr gc`'s business — a consumer asking
    what has been reviewed is not asking about it."""
    (reviews_dir / "widget-99").mkdir()
    (reviews_dir / "widget-99" / "meta.json").write_text('{"repo": "acme/widget"}')

    assert review.listing.rows() == []


def test_a_stray_file_is_not_a_review(reviews_dir):
    (reviews_dir / "check_hunks.py").write_text("# agent scratch\n")

    assert review.listing.rows() == []


def test_a_missing_reviews_root_yields_no_rows(tmp_path, monkeypatch):
    """A machine that has never run a review is not an error."""
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "never-written"))

    assert review.listing.rows() == []


# ── document ────────────────────────────────────────────────────────────────


def test_the_document_echoes_the_version_the_caller_declared(reviews_dir):
    """Echoed rather than restated from a constant, so a document can never
    claim a version its reader did not ask for."""
    _review(reviews_dir, repo="acme/widget", pr_number=42)

    for version in review.listing.SCHEMA_VERSIONS:
        assert review.listing.document(version)["schema_version"] == version


def test_the_document_serialises_every_field_of_a_row(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=42)

    row, = review.listing.document(1)["reviews"]

    assert set(row) == set(review.listing.ReviewRow.__dataclass_fields__)
    assert json.loads(json.dumps(row)) == row, "a row has to survive the wire"


def test_version_one_is_served(reviews_dir):
    """The supported set is allowed to shrink, and dropping 1 is the breaking
    change that has to be a deliberate edit here."""
    assert 1 in review.listing.SCHEMA_VERSIONS


# ── table ───────────────────────────────────────────────────────────────────


def test_the_table_says_so_when_there_is_nothing_to_show():
    assert review.listing.render_table([]) == ["No reviews."]


def test_the_table_names_an_unattributed_review(reviews_dir):
    _review(reviews_dir)

    header, row = review.listing.render_table(review.listing.rows())

    assert header.split() == ["REPO", "PR", "VERDICT", "FINDINGS", "REVIEWED"]
    assert row.startswith("(unattributed)")


def test_the_table_reports_the_pr_and_the_finding_total(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=42,
            reviewed_at="2026-08-18T14:02:11+00:00")

    _, row = review.listing.render_table(review.listing.rows())

    assert row.split() == [
        "acme/widget", "#42", "changes_requested", "3", "2026-08-18T14:02:11+00:00",
    ]


# ── the forge a row came from ───────────────────────────────────────────────


class TestTheTableNamesTheInstance:
    """Two forges can serve one slug, and the table is where they meet.

    `repo` stays the bare slug every consumer keys on — the host rides beside
    it and only the rendered cell is qualified.
    """

    def test_a_row_carries_the_host_from_the_sidecar(self, reviews_dir):
        _review(reviews_dir, repo="acme/widget", pr_number=42,
                host="ghe.acme.com")

        row, = review.listing.rows()

        assert row.host == "ghe.acme.com"
        assert row.repo == "acme/widget", "the slug every consumer keys on is unchanged"

    def test_an_enterprise_row_is_host_qualified(self, reviews_dir):
        _review(reviews_dir, repo="acme/widget", pr_number=42,
                host="ghe.acme.com", reviewed_at="2026-08-18T14:02:11+00:00")

        _, row = review.listing.render_table(review.listing.rows())

        assert row.split()[0] == "ghe.acme.com/acme/widget"

    def test_two_instances_serving_one_slug_are_told_apart(self, reviews_dir):
        """The failure the qualification exists for: identical cells otherwise."""
        _review(reviews_dir, name="widget-42", repo="acme/widget", pr_number=42,
                host="ghe.acme.com")
        _review(reviews_dir, name="widget-43", repo="acme/widget", pr_number=43,
                host="ghe.other.com")

        cells = {r.split()[0] for r in review.listing.render_table(
            review.listing.rows())[1:]}

        assert cells == {"ghe.acme.com/acme/widget", "ghe.other.com/acme/widget"}

    # passes-at-base: asserts the rendering the change was careful not to move
    def test_a_public_github_row_is_unchanged(self, reviews_dir):
        _review(reviews_dir, repo="acme/widget", pr_number=42,
                reviewed_at="2026-08-18T14:02:11+00:00")

        _, row = review.listing.render_table(review.listing.rows())

        assert row.split()[0] == "acme/widget"

    # passes-at-base: an unattributed row has no host to qualify it with
    def test_an_unattributed_row_still_says_so(self, reviews_dir):
        _review(reviews_dir)

        _, row = review.listing.render_table(review.listing.rows())

        assert row.startswith("(unattributed)")


# ── post outcome and history ─────────────────────────────────────────────────

import shutil  # noqa: E402

import review.paths  # noqa: E402


def _post(review_dir, name="post.jsonl", **fields):
    tracking = {"posted_as": "review", "status": "comment", "review_id": 5,
                "posted_at": "2026-08-18T15:00:00Z", **fields}
    (review_dir / name).write_text(json.dumps(tracking) + "\n")


def _archive_current(review_dir, stamp):
    """What `archive_review` leaves for a run, written directly so the test
    controls the stamp."""
    archive = review.paths.archives_dir(review_dir)
    archive.mkdir(exist_ok=True)
    shutil.copy2(review_dir / "review.md", archive / f"{stamp}.md")
    shutil.copy2(review_dir / "review.json", archive / f"{stamp}.record.json")
    shutil.copy2(review_dir / "meta.json", archive / f"{stamp}.meta.json")
    return archive


def test_an_unposted_review_reports_no_post(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=42)
    row, = review.listing.rows()
    assert row.post is None


def test_a_posted_review_reports_its_event(reviews_dir):
    d = _review(reviews_dir, repo="acme/widget", pr_number=42)
    _post(d, status="approve", review_id=77)
    row, = review.listing.rows()
    assert (row.post.event, row.post.review_id) == ("approve", 77)


def test_history_lists_archived_runs_newest_first(reviews_dir):
    d = _review(reviews_dir, repo="acme/widget", pr_number=42, head_sha="old1")
    _archive_current(d, "20260101-120000")
    # Replace the live sidecar in place: rmtree(d) would delete archives/.
    meta = json.loads((d / "meta.json").read_text())
    meta["head_sha"] = "old2"
    (d / "meta.json").write_text(json.dumps(meta))
    archive = _archive_current(d, "20260102-120000")
    _post(archive, name="20260102-120000.post.jsonl", status="dismissed", review_id=0)

    row, = review.listing.rows()

    assert [r.stamp for r in row.history] == ["20260102-120000", "20260101-120000"]
    newest = row.history[0]
    assert newest.structured is True
    assert newest.head_sha == "old2"
    assert newest.findings["total"] == 3
    assert newest.review_file.endswith("archives/20260102-120000.md")
    assert newest.post.event == "dismissed"
    assert row.history[1].post is None


def test_a_run_archived_before_records_is_listed_unstructured(reviews_dir):
    d = _review(reviews_dir, repo="acme/widget", pr_number=42)
    archive = review.paths.archives_dir(d)
    archive.mkdir()
    (archive / "20250101-120000.md").write_text("# an old rendered review")

    row, = review.listing.rows()

    old, = row.history
    assert old.structured is False
    assert (old.head_sha, old.verdict) == ("", "")
    assert old.findings["total"] == 0


def test_a_review_never_rerun_has_no_history(reviews_dir):
    _review(reviews_dir, repo="acme/widget", pr_number=42)
    row, = review.listing.rows()
    assert row.history == []


def test_the_document_names_the_reviews_root(reviews_dir):
    assert review.listing.document(1)["reviews_root"] == str(reviews_dir)


def test_history_serialises_nested(reviews_dir):
    d = _review(reviews_dir, repo="acme/widget", pr_number=42)
    _archive_current(d, "20260101-120000")
    run, = review.listing.document(1)["reviews"][0]["history"]
    assert set(run) == set(review.listing.ReviewRun.__dataclass_fields__)
