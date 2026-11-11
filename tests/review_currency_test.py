"""Tests for `review.currency.self_review_verdict`."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

from pr.self_review_gate import SelfReviewVerdict  # noqa: E402
from review.currency import self_review_verdict  # noqa: E402
from review.document import covers  # noqa: E402
from review.types import ReviewMeta  # noqa: E402
from review_record_support import record, store_record  # noqa: E402


def test_missing_file(tmp_path):
    f = tmp_path / "missing.md"
    v = self_review_verdict(f, "abc")
    assert v == SelfReviewVerdict(False, "no self-review yet", review_file=str(f))


def test_review_md_without_review_json(tmp_path):
    f = tmp_path / "review.md"
    f.write_text("# Review\n")
    v = self_review_verdict(f, "abc")
    assert v.covered is False
    assert v.reason == "self-review predates review.json"
    assert v.review_file == str(f)
    assert v.reviewed_through == ""


def test_exact_head(tmp_path):
    f = tmp_path / "review.md"
    sha = "abcdef0123456789"
    store_record(f, record(), ReviewMeta(head_sha=sha))
    v = self_review_verdict(f, sha)
    assert v.covered is True
    assert v.reason == "self-review is current"
    assert v.review_file == str(f)
    assert v.reviewed_through == sha


def test_covered_sha_counts(tmp_path):
    f = tmp_path / "review.md"
    covered = "bbbbbbb2222aaaa"
    store_record(f, record(), ReviewMeta(head_sha="aaaaaaa1111bbbb", covered_sha=covered))
    v = self_review_verdict(f, covered)
    assert v.covered is True
    assert v.reviewed_through == covered


def test_moved_head_reason_contains_abbrev(tmp_path):
    f = tmp_path / "review.md"
    store_record(f, record(), ReviewMeta(head_sha="1111111abcdef"))
    v = self_review_verdict(f, "2222222aaaa")
    assert v.covered is False
    assert "1111111" in v.reason
    assert v.reviewed_through == "1111111abcdef"


def test_abbreviation_at_least_7_covers(tmp_path):
    f = tmp_path / "review.md"
    store_record(f, record(), ReviewMeta(head_sha="abcdef0"))
    subject = "abcdef0123456"
    assert covers("abcdef0", subject) is True
    v = self_review_verdict(f, subject)
    assert v.covered is True


def test_abbreviation_under_7_does_not_cover(tmp_path):
    f = tmp_path / "review.md"
    store_record(f, record(), ReviewMeta(head_sha="abc"))
    subject = "abcdef0123456"
    assert covers("abc", subject) is False
    v = self_review_verdict(f, subject)
    assert v.covered is False
    assert v.reason == "self-review is of abc"
