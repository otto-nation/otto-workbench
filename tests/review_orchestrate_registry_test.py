"""Prompt assembly: `review.registry.build_prompt` preflight and `review.prompt` stats."""

import json
import sys
from dataclasses import replace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401

from core.phases import Mode, Phase
from review.artifacts import DisproveArtifact, GroupArtifact, ReviewArtifact, output_format
from review.prompt import build_common_sections
from review.store import StoredReview
from review.types import ReviewMeta
import review.registry
from review_prompt_support import MAX_PROMPT_BYTES, _make_job, _make_preflight
from review_record_support import finding, record
from conftest import TEST_MODEL as _TEST_MODEL, model_budget_bytes

_TEST_BUDGET = model_budget_bytes()


# ── Prompt stats persistence ────────────────────────────────────────────────


class TestPromptStats:
    def test_prompt_stats_written(self, ro, tmp_path):
        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main",
            head_sha="abc", additions=1, deletions=0,
            changed_files=1, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "s.jsonl"),
        )

        ro.log_prompt_size(
            "test", "hello world", {"sec": "data"}, job,
            budget_bytes=_TEST_BUDGET, model=_TEST_MODEL,
        )

        stats_file = tmp_path / ro.FILENAME_PROMPT_STATS
        assert stats_file.exists()
        stats = json.loads(stats_file.read_text())
        assert isinstance(stats, list)
        assert stats[0]["template"] == "test"
        assert stats[0]["prompt_bytes"] == len(b"hello world")
        assert "utilization_pct" in stats[0]
        assert stats[0]["sections"]["sec"] == len(b"data")

    def test_prompt_stats_appends(self, ro, tmp_path):
        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main",
            head_sha="abc", additions=1, deletions=0,
            changed_files=1, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "s.jsonl"),
        )

        ro.log_prompt_size("first", "aaa", {}, job, budget_bytes=_TEST_BUDGET, model=_TEST_MODEL)
        ro.log_prompt_size("second", "bbb", {}, job, budget_bytes=_TEST_BUDGET, model=_TEST_MODEL)

        stats = json.loads((tmp_path / ro.FILENAME_PROMPT_STATS).read_text())
        assert len(stats) == 2
        assert stats[0]["template"] == "first"
        assert stats[1]["template"] == "second"

    def test_prompt_stats_survives_corrupt_file(self, ro, tmp_path):
        pr = ro.PRMetadata(
            title="t", body="", head="feat", base="main",
            head_sha="abc", additions=1, deletions=0,
            changed_files=1, files=[],
        )
        ctx = ro.PRContext()
        review_file = str(tmp_path / "review.md")
        job = ro.ReviewJob(
            repo="r", pr_number="1", pr=pr, ctx=ctx,
            wt_path=str(tmp_path), review_file=review_file,
            session_log=str(tmp_path / "s.jsonl"),
        )

        stats_file = tmp_path / ro.FILENAME_PROMPT_STATS
        stats_file.write_text("")

        ro.log_prompt_size("test", "hello", {}, job, budget_bytes=_TEST_BUDGET, model=_TEST_MODEL)

        stats = json.loads(stats_file.read_text())
        assert isinstance(stats, list)
        assert len(stats) == 1
        assert stats[0]["template"] == "test"


# ── build_prompt (preflight) ──────────────────────────────────────────


class TestBuildPromptPreflight:
    def test_includes_preflight_data_when_set(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="desc", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix it", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="abc fix",
            file_contents={"a.go": "package main"},
            file_permissions={"a.go": "0o644"},
            instructions_md="# Project",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "Pre-collected data" in result
        assert "package main" in result
        assert "--- a/a.go" in result

    def test_no_preflight_data_when_not_set(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="desc", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix it", reviews="[]",
            review_comments="[]", comments="[]",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="99", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "Pre-collected data" not in result

    def test_synthesis_includes_reviews_section(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix",
            reviews='[{"user":"bob","state":"APPROVED"}]',
            review_comments="[]",
            comments="[]",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
        )
        result = ro.build_prompt(
            ro.Phase.SYNTHESIS, job, max_turns=15,
            holistic_content="assessment",
            group_count=1,
            merged_content="## Must fix\n- [M1] bug",
        )
        assert "bob" in result
        assert "APPROVED" in result

    def test_group_template_gets_scoped_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=20, deletions=10, changed_files=2,
            files=[
                {"path": "a.go", "additions": 10, "deletions": 5},
                {"path": "b.go", "additions": 10, "deletions": 5},
            ],
        )
        ctx = ro.PRContext()
        preflight = ro.PreflightData(
            diff="full diff here",
            commit_log="commits",
            file_contents={"a.go": "package a", "b.go": "package b"},
            file_permissions={"a.go": "0o644", "b.go": "0o644"},
            instructions_md="",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(
            ro.Phase.GROUP, job, max_turns=15,
            group_idx=1, group_count=2, group_name="pkg",
            group_files_formatted="  - a.go (+10 -5)",
            holistic_content="",
            group_file_paths=["a.go"],
        )
        assert "package a" in result
        assert "package b" not in result

    def test_holistic_template_includes_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="abc fix",
            file_contents={"a.go": "package main"},
            file_permissions={"a.go": "0o644"},
            instructions_md="# Proj",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(ro.Phase.HOLISTIC, job, max_turns=15)
        assert "Pre-collected data" in result
        assert "package main" in result

    def test_self_review_template_includes_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext()
        preflight = ro.PreflightData(
            diff="--- a/a.go\n+++ b/a.go",
            commit_log="abc fix",
            file_contents={"a.go": "package main"},
            file_permissions={"a.go": "0o644"},
            instructions_md="",
            architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight, mode=ro.Mode.SELF,
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "Pre-collected data" in result
        assert "package main" in result

    def test_env_section_all_files_pre_collected(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="diff", commit_log="log",
            file_contents={"a.go": "pkg"},
            file_permissions={"a.go": "0o644"},
            instructions_md="", architecture_md="",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "NOT in the PR" in result
        assert "Files not pre-collected" not in result
        assert "Read source files directly" not in result

    def test_env_section_partial_preflight_with_omitted_files(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=2,
            files=[
                {"path": "a.go", "additions": 5, "deletions": 2},
                {"path": "b.go", "additions": 5, "deletions": 3},
            ],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        preflight = ro.PreflightData(
            diff="diff", commit_log="log",
            file_contents={"a.go": "pkg"},
            file_permissions={"a.go": "0o644"},
            instructions_md="", architecture_md="",
            omitted_files=["b.go"],
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
            preflight=preflight,
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "NOT in the PR" not in result
        assert "must be read directly" in result
        assert "Read source files directly" not in result

    def test_env_section_no_preflight(self, ro):
        pr = ro.PRMetadata(
            title="Fix", body="", head="feat", base="main", head_sha="abc",
            additions=10, deletions=5, changed_files=1,
            files=[{"path": "a.go", "additions": 10, "deletions": 5}],
        )
        ctx = ro.PRContext(
            commits="fix", reviews="[]",
            review_comments="[]", comments="[]",
        )
        job = ro.ReviewJob(
            repo="org/repo", pr_number="1", pr=pr, ctx=ctx,
            wt_path="/tmp/wt", review_file="/tmp/review.md",
            session_log="/tmp/session.jsonl",
        )
        result = ro.build_prompt(ro.Phase.SINGLE, job, max_turns=15)
        assert "NOT in the PR" not in result
        assert "must be read directly" not in result
        assert "Read source files directly" in result


def test_the_output_block_carries_the_format_after_the_write_recipe():
    job = _make_job(_make_preflight())
    common = build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES)
    built = review.registry.for_phase(Phase.DISPROVE).build(
        job, common, {"artifact": DisproveArtifact}, "/tmp/d.json",
    )
    block = built.builder.vars["output_block"]
    assert block.startswith("Write your output to: /tmp/d.json")
    assert block.endswith("\n\n" + output_format(DisproveArtifact, Mode.PR, rereview=False))


def test_build_prompt_hands_the_builder_its_artifact(monkeypatch):
    seen = {}
    monkeypatch.setattr(review.registry, "fit_rendered_prompt", lambda phase, job, **kw: seen.update(kw) or "p")
    monkeypatch.setattr(review.registry, "selected_backend", lambda: None)
    monkeypatch.setattr(review.registry, "phase_model", lambda *a, **k: "model")
    entry = review.registry._PHASES[Phase.DISPROVE]
    monkeypatch.setitem(review.registry._PHASES, Phase.DISPROVE, replace(entry, artifact=DisproveArtifact))
    review.registry.build_prompt(Phase.DISPROVE, _make_job(_make_preflight()), max_turns=5, review_content="x")
    assert seen["extra"]["artifact"] is DisproveArtifact


def test_the_single_prompt_shows_the_carry_fields_only_with_a_prior_section():
    """D20: the builder that built the prior section decides; `job.prior` alone over-shows."""
    job = _make_job(_make_preflight())
    common = build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES)
    build = review.registry.for_phase(Phase.SINGLE).build
    first = build(job, common, {"artifact": ReviewArtifact}, "/tmp/s.json").builder.vars["output_block"]
    job.prior = StoredReview(record(finding("M1", "still broken")), ReviewMeta())
    again = build(job, common, {"artifact": ReviewArtifact}, "/tmp/s.json").builder.vars["output_block"]
    assert "`findings[].sid`" not in first and "`prior_findings`" not in first
    assert "`findings[].sid`" in again and "`prior_findings`" in again


def test_the_group_prompt_shows_the_carry_fields_only_for_a_prior_finding_in_its_files():
    """D20: a prior finding outside the group's files builds no prior section, so no carry fields."""
    job = _make_job(_make_preflight())
    job.prior = StoredReview(record(finding("M1", "still broken", path="a.py", line=1)), ReviewMeta())
    common = build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES)
    build = review.registry.for_phase(Phase.GROUP).build

    def output_block(files: list[str]) -> str:
        extra = {
            "artifact": GroupArtifact, "group_idx": 1, "group_count": 1, "group_name": "g",
            "group_files_formatted": "\n".join(files), "group_file_paths": files,
        }
        return build(job, common, extra, "/tmp/group-1.json").builder.vars["output_block"]

    outside, inside = output_block(["b.py"]), output_block(["a.py"])
    assert "`findings[].sid`" not in outside and "`prior_findings`" not in outside
    assert "`findings[].sid`" in inside and "`prior_findings`" in inside


def test_the_synthesis_prompt_shows_the_carry_fields_only_on_a_re_review():
    """D20: synthesis has no `## Prior review` section to test; a prior round is what it carries."""
    job = _make_job(_make_preflight())
    common = build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES)
    build = review.registry.for_phase(Phase.SYNTHESIS).build
    extra = {"artifact": ReviewArtifact, "group_count": 2, "merged_content": "```json\n{}\n```"}
    first = build(job, common, extra, "/tmp/synthesis.json").builder.vars["output_block"]
    job.prior = StoredReview(record(finding("M1", "still broken")), ReviewMeta())
    again = build(job, common, extra, "/tmp/synthesis.json").builder.vars["output_block"]
    assert "`findings[].sid`" not in first and "`prior_findings`" not in first
    assert "`findings[].sid`" in again and "`prior_findings`" in again


@pytest.mark.parametrize("mode", [Mode.PR, Mode.SELF])
def test_the_synthesis_prompt_carries_the_issue_section(mode):
    job = _make_job(_make_preflight(), mode=mode)
    job.issue_link = "https://x/1"
    job.issue_context = "BODY-MARKER"
    result = review.registry.build_prompt(
        Phase.SYNTHESIS, job, max_turns=15, group_count=2, merged_content="x",
    )
    assert "https://x/1" in result
    assert "BODY-MARKER" in result
