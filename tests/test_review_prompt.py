"""Tests for review_prompt: scoped prompt section builders."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "claude" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review_preflight import PRContext, PRMetadata, PreflightData
from review_prompt import _build_delta_section, _build_pr_header


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
        header = _build_pr_header(_make_pr(), _make_ctx())
        assert "+100 -50 across 3 files" in header

    def test_filter_scopes_size_line(self):
        header = _build_pr_header(_make_pr(), _make_ctx(), file_filter=["a.py"])
        assert "+40 -20 across 1 files" in header
        assert "of 3 total" in header

    def test_filter_scopes_file_breakdown(self):
        pr = _make_pr(additions=600, deletions=100)
        header = _build_pr_header(pr, _make_ctx(), file_filter=["a.py", "b.py"])
        assert "a.py" in header
        assert "b.py" in header
        assert "c.py" not in header

    def test_filter_always_includes_file_breakdown(self):
        pr = _make_pr(additions=10, deletions=5)
        header_unfiltered = _build_pr_header(pr, _make_ctx())
        assert "File breakdown" not in header_unfiltered

        header_filtered = _build_pr_header(pr, _make_ctx(), file_filter=["a.py"])
        assert "File breakdown" in header_filtered

    def test_filter_preserves_description_and_commits(self):
        header = _build_pr_header(_make_pr(), _make_ctx(), file_filter=["a.py"])
        assert "Description" in header
        assert "feat: stuff" in header
