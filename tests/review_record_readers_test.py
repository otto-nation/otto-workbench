"""Every reader answers from review.json and meta.json; a directory without the record is stale."""

import argparse
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import batch.outcomes
import core.log
import review.post_file
from review.document import ReviewDocument
from review.paths import FILENAME_RECORD
from review.record import load_record
from review.recover import read_review_sha
from review.sections import ReviewSections
from review.summary import build_review_summary
from review.types import ReviewMeta

from review_record_support import fixture_meta, fixture_text, store_review


def test_a_bare_review_md_is_stale(tmp_path):
    (tmp_path / "review.md").write_text(fixture_text("synthesis_pr", "input.md"))
    assert ReviewDocument.read(tmp_path / "review.md") is None
    assert read_review_sha(tmp_path / "review.md") == ""


def test_the_head_sha_is_meta_json_s(tmp_path):
    path = store_review(tmp_path / "review.md", fixture_text("synthesis_pr", "input.md"), fixture_meta("synthesis_pr"))
    assert read_review_sha(path) == "2cb613ec084559a9590f074eabde546c44b284a3"


def test_editing_review_md_changes_nothing_a_reader_sees(tmp_path):
    path = store_review(tmp_path / "review.md", fixture_text("synthesis_pr", "input.md"), fixture_meta("synthesis_pr"))
    path.write_text(path.read_text().replace("**[S1]**", "**[M9]**"))
    assert [f.id for f in ReviewDocument.read(path).findings][0] == "S1"


def test_posting_sections_come_from_the_record(tmp_path):
    path = store_review(tmp_path / "review.md", fixture_text("synthesis_pr", "input.md"), fixture_meta("synthesis_pr"))
    sections = ReviewSections.from_record(ReviewDocument.read(path).record)
    assert sections.get("summary").startswith("Adds speech-to-text")
    assert sections.get("verdict").startswith("**Approve.**")
    assert "disprove" in sections.get("agent_failures")


def test_batch_plan_calls_a_stale_self_review_stale(tmp_path):
    import batch.plan
    (tmp_path / "review.md").write_text("# old\n")
    need = batch.plan.review_need(tmp_path / "review.md", "abc")
    assert need.needed and "predates review.json" in need.reason


def test_a_record_missing_schema_version_loads_as_none(tmp_path):
    (tmp_path / FILENAME_RECORD).write_text(json.dumps({"summary": "x"}))
    assert load_record(tmp_path) is None


def test_undecodable_json_loads_as_none_without_a_schema_warning(tmp_path):
    (tmp_path / FILENAME_RECORD).write_text("{not json")
    warned = []
    with patch.object(core.log, "warn", side_effect=warned.append):
        assert load_record(tmp_path) is None
    assert warned
    assert any("could not" in msg for msg in warned)
    assert not any("schema None" in msg for msg in warned)


def test_retro_skips_a_directory_without_review_json(tmp_path):
    from retro.reviews import scan_local_reviews

    reviews_dir = tmp_path / "reviews" / "myrepo-self-1"
    reviews_dir.mkdir(parents=True)
    (reviews_dir / "review.md").write_text(
        "# Self-Review: myrepo — branch\n"
        "\n"
        "## Must fix\n"
        "\n"
        "- [ ] **[M1]** `src/main.go` — Missing error handling\n"
    )
    scan = scan_local_reviews(tmp_path / "reviews", [], {})
    assert scan.consumed == []
    assert scan.repos == []


_STORE_TEXT = "## Should fix\n- **[S1]** path:1 — from json\n"
_MD_LIE = "## Must fix\n- **[M9]** path:9 — from md\n"


def test_summary_of_a_stale_dir_ignores_review_md(tmp_path):
    review = tmp_path / "review.md"
    review.write_text(_MD_LIE)
    assert build_review_summary("org/repo", "42", str(review)) is None


def test_summary_answers_from_review_json(tmp_path):
    path = store_review(tmp_path / "review.md", _STORE_TEXT)
    path.write_text(_MD_LIE)
    report = build_review_summary("org/repo", "42", str(path))
    assert report.findings["must_fix"] == 0
    assert report.findings["should_fix"] == 1
    assert report.findings["total"] == 1


def test_open_findings_of_a_stale_dir_is_empty(tmp_path, monkeypatch):
    path = tmp_path / "review.md"
    path.write_text(_MD_LIE)
    monkeypatch.setattr(
        batch.outcomes.review.paths, "self_review_file_path", lambda repo, branch: path,
    )
    item = SimpleNamespace(repo="o/r", branch="b")
    assert batch.outcomes.open_findings(item) == []


def test_open_findings_answers_from_review_json(tmp_path, monkeypatch):
    path = store_review(tmp_path / "review.md", _STORE_TEXT)
    path.write_text(_MD_LIE)
    monkeypatch.setattr(
        batch.outcomes.review.paths, "self_review_file_path", lambda repo, branch: path,
    )
    item = SimpleNamespace(repo="o/r", branch="b")
    found = batch.outcomes.open_findings(item)
    assert found == [
        {"severity": "should-fix", "title": "path:1 — from json", "declined": False},
    ]


def _post_args():
    return argparse.Namespace(
        severity="M,S,N,I", dry_run=True, pr="1", chunk_size=30,
    )


def test_run_post_stale_dir_returns_1_and_logs_predates(tmp_path):
    path = tmp_path / "review.md"
    path.write_text(_MD_LIE)
    logged = []
    with patch.object(core.log, "error", side_effect=logged.append):
        rc = review.post_file.run_post(
            MagicMock(), _post_args(), "org/repo", ReviewMeta(), path,
        )
    assert rc == 1
    assert any("predates review.json" in msg for msg in logged)


def test_run_post_answers_from_review_json(tmp_path, capsys):
    path = store_review(tmp_path / "review.md", _STORE_TEXT)
    path.write_text(_MD_LIE)
    with patch.object(review.post_file, "_get_diff", return_value=""):
        rc = review.post_file.run_post(
            MagicMock(), _post_args(), "org/repo", ReviewMeta(), path,
        )
    out = capsys.readouterr().out
    assert rc == 0
    assert "from json" in out
    assert "from md" not in out
