"""Tests for review_prompt: scoped prompt section builders."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_preflight import (
    PRContext, PRMetadata, PreflightData, ReviewJob,
    MAX_PROMPT_BYTES, MIN_DIFF_BYTES,
)
from review_common import (
    Effort,
    TEMPLATE_HOLISTIC, TEMPLATE_SCOUT, TEMPLATE_SELF_SYNTHESIS, TEMPLATE_SYNTHESIS,
)
from review_prompt import (
    _PROMPT_HANDLERS, _build_common_sections, _build_delta_section, _build_pr_header,
    _compute_diff_budget,
)


# ── _build_delta_section with file_filter ──────────────────────────────────


def _make_preflight(**overrides):
    defaults = dict(
        diff="", commit_log="", file_contents={"a.py": "x", "b.py": "y"},
        file_permissions={}, claude_md="", architecture_md="",
        omitted_files=[],
        prior_head_sha="abc1234def",
        delta_files=["a.py", "b.py"],
        delta_commit_log="feat: stuff",
        delta_diff=(
            "diff --git a/a.py b/a.py\n"
            "--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n"
            "diff --git a/b.py b/b.py\n"
            "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-old\n+new\n"
        ),
    )
    defaults.update(overrides)
    return PreflightData(**defaults)


class TestBuildDeltaSectionScoped:
    def test_no_filter_includes_all(self):
        pf = _make_preflight()
        section = _build_delta_section(pf)
        assert "`a.py`" in section
        assert "`b.py`" in section
        assert "a/a.py" in section
        assert "a/b.py" in section

    def test_filter_scopes_delta_files(self):
        pf = _make_preflight()
        section = _build_delta_section(pf, file_filter=["a.py"])
        assert "`a.py`" in section
        assert "a/a.py" in section
        assert "a/b.py" not in section

    def test_filter_scopes_unchanged_to_filter_set(self):
        pf = _make_preflight(delta_files=["a.py"])
        section = _build_delta_section(pf, file_filter=["a.py", "b.py"])
        assert "### Files modified" in section
        assert "`a.py`" in section
        assert "### Files unchanged" in section
        assert "`b.py`" in section

    def test_filter_excludes_commit_log(self):
        pf = _make_preflight()
        unfiltered = _build_delta_section(pf)
        filtered = _build_delta_section(pf, file_filter=["a.py"])
        assert "feat: stuff" in unfiltered
        assert "feat: stuff" not in filtered

    def test_no_preflight_returns_empty(self):
        assert _build_delta_section(None) == ""
        assert _build_delta_section(None, file_filter=["a.py"]) == ""

    def test_no_prior_sha_returns_empty(self):
        pf = _make_preflight(prior_head_sha="")
        assert _build_delta_section(pf, file_filter=["a.py"]) == ""


# ── _build_pr_header with file_filter ──────────────────────────────────────


def _make_pr(**overrides):
    defaults = dict(
        title="Test PR", body="Description", head="feat", base="main",
        head_sha="abc123", additions=100, deletions=50, changed_files=3,
        files=[
            {"path": "a.py", "additions": 40, "deletions": 20},
            {"path": "b.py", "additions": 30, "deletions": 15},
            {"path": "c.py", "additions": 30, "deletions": 15},
        ],
    )
    defaults.update(overrides)
    return PRMetadata(**defaults)


def _make_ctx(**overrides):
    defaults = dict(commits="abc feat: stuff")
    defaults.update(overrides)
    return PRContext(**defaults)


class TestBuildPrHeaderScoped:
    def test_no_filter_shows_full_size(self):
        header = _build_pr_header(_make_pr(), _make_ctx(), Effort.MEDIUM)
        assert "+100 -50 across 3 files" in header

    def test_filter_scopes_size_line(self):
        header = _build_pr_header(
            _make_pr(), _make_ctx(), Effort.MEDIUM, file_filter=["a.py"],
        )
        assert "+40 -20 across 1 files" in header
        assert "of 3 total" in header

    def test_filter_scopes_file_breakdown(self):
        pr = _make_pr(additions=600, deletions=100)
        header = _build_pr_header(
            pr, _make_ctx(), Effort.MEDIUM, file_filter=["a.py", "b.py"],
        )
        assert "a.py" in header
        assert "b.py" in header
        assert "c.py" not in header

    def test_filter_always_includes_file_breakdown(self):
        pr = _make_pr(additions=10, deletions=5)
        header_unfiltered = _build_pr_header(pr, _make_ctx(), Effort.MEDIUM)
        assert "File breakdown" not in header_unfiltered

        header_filtered = _build_pr_header(
            pr, _make_ctx(), Effort.MEDIUM, file_filter=["a.py"],
        )
        assert "File breakdown" in header_filtered

    def test_filter_preserves_description_and_commits(self):
        header = _build_pr_header(
            _make_pr(), _make_ctx(), Effort.MEDIUM, file_filter=["a.py"],
        )
        assert "Description" in header
        assert "feat: stuff" in header

    def test_low_effort_suppresses_a_breakdown_medium_would_show(self):
        """The regression #622 fixes: the same PR, classified by the preset."""
        pr = _make_pr(additions=600, deletions=150)
        assert "File breakdown" in _build_pr_header(
            pr, _make_ctx(), Effort.MEDIUM,
        )
        assert "File breakdown" not in _build_pr_header(
            pr, _make_ctx(), Effort.LOW,
        )


# ── _compute_diff_budget ────────────────────────────────────────────────────


def _make_job(preflight=None):
    pr = PRMetadata(
        title="T", body="B", head="h", base="main", head_sha="abc",
        additions=10, deletions=5, changed_files=1,
        files=[{"path": "a.py", "additions": 10, "deletions": 5}],
    )
    ctx = PRContext(commits="abc feat")
    return ReviewJob(
        repo="r", pr_number="1", pr=pr, ctx=ctx,
        wt_path="/tmp/w", review_file="/tmp/r.md",
        session_log="/tmp/l.jsonl",
        preflight=preflight,
    )


class TestComputeDiffBudget:
    def test_returns_remaining_when_within_budget(self):
        job = _make_job(_make_preflight(claude_md="", architecture_md=""))
        sections = {"header": "small"}
        result = _compute_diff_budget(job, sections)
        assert result > MIN_DIFF_BYTES

    def test_clamps_to_min_diff_by_default(self):
        huge = "x" * (MAX_PROMPT_BYTES + 1000)
        job = _make_job(_make_preflight(claude_md=huge))
        sections = {"header": "small"}
        result = _compute_diff_budget(job, sections)
        assert result == MIN_DIFF_BYTES

    def test_min_diff_zero_allows_zero_budget(self):
        huge = "x" * (MAX_PROMPT_BYTES + 1000)
        job = _make_job(_make_preflight(claude_md=huge))
        sections = {"header": "small"}
        result = _compute_diff_budget(job, sections, min_diff=0)
        assert result == 0

    def test_skip_file_contents_frees_budget(self):
        big_content = "y" * 200_000
        pf = _make_preflight(file_contents={"gen.pb.go": big_content})
        job = _make_job(pf)
        sections = {"header": "small"}
        with_fc = _compute_diff_budget(job, sections, file_filter=["gen.pb.go"])
        without_fc = _compute_diff_budget(
            job, sections, file_filter=["gen.pb.go"], skip_file_contents=True,
        )
        assert without_fc > with_fc
        assert without_fc - with_fc >= 200_000


# ── Shared prompt bodies ────────────────────────────────────────────────────


class TestSharedPromptBodies:
    """The paired handlers must stay interchangeable apart from their variant."""

    def _vars(self, template, **extra):
        job = _make_job(_make_preflight())
        common = _build_common_sections(job, max_turns=10)
        builder, _ = _PROMPT_HANDLERS[template](job, common, extra)
        return builder.vars

    def test_scout_and_holistic_differ_only_in_output_target(self):
        holistic = self._vars(TEMPLATE_HOLISTIC, holistic_output="/tmp/h.md")
        scout = self._vars(TEMPLATE_SCOUT, scout_output="/tmp/s.md")
        assert holistic.keys() == scout.keys()
        differing = [k for k in holistic if holistic[k] != scout[k]]
        assert differing == ["output_block"]

    def test_synthesis_variants_differ_only_in_identity_and_prior_reviews(self):
        shared_extra = dict(group_count=2, merged_content="m", holistic_content="h")
        pr = self._vars(TEMPLATE_SYNTHESIS, **shared_extra)
        self_ = self._vars(TEMPLATE_SELF_SYNTHESIS, **shared_extra)
        assert set(pr) - set(self_) == {"pr_number", "pr_title", "reviews_section"}
        assert set(self_) - set(pr) == {"branch_name"}
        common_keys = set(pr) & set(self_)
        assert all(pr[k] == self_[k] for k in common_keys)
