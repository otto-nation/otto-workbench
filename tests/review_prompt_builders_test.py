"""review.prompt_sections builders: delta, PR header, CI failures and dropped-contents guidance."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.budget import FileFit, MAX_DELTA_LIST_ENTRIES, fit_files
from review.collect import format_preflight_data
from gh.types import PRContext, PRMetadata
from core.phases import Effort

from review.prompt_sections import (
    _build_ci_failure_items, _build_delta_section, _build_env_section,
    _build_omitted_guidance, _build_pr_header,
)
from pr.ci_failures import FailureGroup, FailureItem, FailureKind, RunState
from pr.domains import CIDomain

from review_prompt_support import _make_preflight


# ── _build_delta_section with file_filter ──────────────────────────────────


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
        assert "### Files the author modified" in section
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


class TestBuildDeltaSectionBounded:
    """The section cannot spend the prompt on lists or on the delta diff."""

    def test_file_lists_are_capped_and_say_so(self):
        """A rebase-inflated delta is a summary, not 5,000 lines of paths.

        The one that prompted this budgeted 4,974 delta files against a
        107-file PR: 260KB of `- \\`path\\`` lines, which pushed synthesis 75%
        past its budget on their own.
        """
        many = [f"pkg/f{i:05d}.go" for i in range(4_974)]
        pf = _make_preflight(delta_files=many, delta_diff="", file_contents={})
        section = _build_delta_section(pf)
        assert section.count("\n- `") == MAX_DELTA_LIST_ENTRIES
        assert f"+{4_974 - MAX_DELTA_LIST_ENTRIES} more not listed" in section
        assert len(section.encode()) < 20_000

    def test_max_bytes_shrinks_the_delta_diff(self):
        big = "".join(
            f"diff --git a/f{i}.py b/f{i}.py\n@@ -1 +1 @@\n-old\n+{'x' * 500}\n"
            for i in range(200)
        )
        pf = _make_preflight(delta_diff=big, delta_files=[f"f{i}.py" for i in range(200)])
        unbounded = _build_delta_section(pf)
        bounded = _build_delta_section(pf, max_bytes=40_000)
        assert len(bounded.encode()) < len(unbounded.encode())
        assert len(bounded.encode()) <= 40_000

    def test_no_room_drops_the_diff_rather_than_a_fragment(self):
        pf = _make_preflight()
        section = _build_delta_section(pf, max_bytes=100)
        assert "### Delta diff" not in section
        assert "Incremental review context" in section


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
        """The same PR, classified by the preset."""
        pr = _make_pr(additions=600, deletions=150)
        assert "File breakdown" in _build_pr_header(
            pr, _make_ctx(), Effort.MEDIUM,
        )
        assert "File breakdown" not in _build_pr_header(
            pr, _make_ctx(), Effort.LOW,
        )


# ── _build_ci_failure_items ─────────────────────────────────────────────────


class TestBuildCiFailureItems:
    def _make_run(self, run_id=999):
        item = FailureItem(
            id="sc2086-bin-foo-42",
            annotation="SC2086: Double quote to prevent globbing",
            file="bin/foo.sh", line=42,
            diagnosis=None, fix_sha=None, outcome=None,
        )
        group = FailureGroup(job="lint / shellcheck", kind=FailureKind.LINT, items=(item,))
        return RunState(
            run_id=run_id, run_number=1, head_sha="abc", status="completed",
            conclusion="failure", fetched_at="2026-08-12T00:00:00+00:00",
            failures={"shellcheck": group},
        )

    def test_returns_failure_items_for_the_latest_run(self):
        ci = CIDomain(latest_run_id=999, runs={999: self._make_run()})
        pr = _make_pr(files=[{"path": "bin/foo.sh", "additions": 1, "deletions": 0}])
        items = _build_ci_failure_items(ci, pr)
        assert items
        assert any("bin/foo.sh:42" in item for item in items)
        assert any("SC2086" in item for item in items)


# ── Dropped file contents are declared ──────────────────────────────────────


class TestDroppedContentsAreDeclared:
    """What the budget drops, the prompt has to admit to dropping.

    Dropping the contents used to drop the list of them along with it, while
    the environment section went on saying "File contents and diffs are in the
    Pre-collected data section" — so the agent was told its files were in the
    prompt and shown neither them nor their names.
    """

    def test_skipping_contents_still_names_the_files(self):
        pf = _make_preflight(file_contents={"a.py": "x", "b.py": "y"})
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        text = format_preflight_data(pf, files=dropped_all).text
        assert "### Changed file contents" not in text
        assert "### Files not pre-collected (read directly)" in text
        assert "- a.py" in text
        assert "- b.py" in text

    def test_skipping_contents_does_not_double_list_omitted_files(self):
        pf = _make_preflight(file_contents={"a.py": "x"}, omitted_files=["big.go"])
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        text = format_preflight_data(pf, files=dropped_all).text
        assert text.count("- big.go") == 1
        assert "- a.py" in text

    def test_env_section_sends_the_agent_to_the_worktree(self):
        pf = _make_preflight()
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        kept = _build_env_section("/tmp/w", preflight=pf)
        dropped = _build_env_section("/tmp/w", preflight=pf, files=dropped_all)
        assert "File contents and diffs are in the Pre-collected data section" in kept
        assert "file contents are not" in dropped
        assert "Files not pre-collected" in dropped

    def test_nothing_to_fit_is_not_a_drop(self):
        """An empty fit with nothing omitted is not the same as a fit that lost everything.

        `_fit_budget` starts every plan at `FileFit(scoped, ..., [])`, so a
        `file_filter` scoping a section to zero collected files produces this
        exact shape with no budget cut involved. Reading it as "dropped
        everything" tells the agent to batch-read a "Files not pre-collected"
        list that was never populated.
        """
        pf = _make_preflight(file_contents={}, omitted_files=[])
        nothing_to_fit = FileFit({}, {}, [])
        text = _build_env_section("/tmp/w", preflight=pf, files=nothing_to_fit)
        assert "File contents and diffs are in the Pre-collected data section" in text
        assert "file contents are not" not in text

    def test_omitted_guidance_asks_for_the_batch_read(self):
        pf = _make_preflight(omitted_files=[])
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        assert _build_omitted_guidance(pf) == ""
        assert "Files not pre-collected" in _build_omitted_guidance(
            pf, files=dropped_all,
        )

    def test_skip_omitted_does_not_silence_dropped_contents(self):
        """The effort preset declines the large files, not every file."""
        pf = _make_preflight(omitted_files=["big.go"])
        dropped_all = fit_files(pf.file_contents, pf.file_permissions, 0)
        assert "not reviewed at this effort level" in _build_omitted_guidance(
            pf, skip_omitted=True,
        )
        assert "Files not pre-collected" in _build_omitted_guidance(
            pf, skip_omitted=True, files=dropped_all,
        )
