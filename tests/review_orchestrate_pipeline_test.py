"""PR metadata in `review.pipeline`: the local-diff overlay and self-mode metadata fetch."""

import sys
from pathlib import Path

import pytest

from conftest import add_self_origin, commit_all, git_out, init_repo

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

# The module the `ro` fixture returns, imported so bin/local/select-pytest maps
# this suite to the code it reaches through `ro`.
import cli.review_orchestrate  # noqa: F401

import review.phases
import review.pipeline
from review.artifacts import MAX_DERIVED_FOCUS
from review.budget import (
    DERIVED_AREA_RESERVE_BYTES, MIN_DIFF_BYTES, TEMPLATE_OVERHEAD_BYTES,
    fixed_preflight_bytes,
)
from review.prompt_sections import _build_focus_section
from review.types import PreflightData
from review_focus_support import area, user_area
from review_prompt_support import _make_job


# ── self-review metadata ──────────────────────────────────────────────


def _pr_metadata(ro, **overrides):
    defaults = dict(
        title="feat: thing", body="why", head="feat", base="main",
        head_sha="a" * 40, additions=1, deletions=0, changed_files=1,
        files=[{"path": "pushed.go", "additions": 1, "deletions": 0}],
    )
    return ro.PRMetadata(**{**defaults, **overrides})


class TestWithLocalDiff:
    def test_diff_surface_comes_from_the_worktree(self, ro):
        pr = _pr_metadata(ro)
        local = _pr_metadata(
            ro, head_sha="b" * 40, additions=9, deletions=2, changed_files=2,
            files=[
                {"path": "pushed.go", "additions": 1, "deletions": 0},
                {"path": "unpushed.go", "additions": 8, "deletions": 2},
            ],
        )

        merged = ro._with_local_diff(pr, local)

        assert merged.head_sha == "b" * 40
        assert [f["path"] for f in merged.files] == ["pushed.go", "unpushed.go"]
        assert merged.additions == 9
        assert merged.deletions == 2
        assert merged.changed_files == 2

    def test_branch_name_comes_from_the_worktree(self, ro):
        pr = _pr_metadata(ro, head="feat")
        local = _pr_metadata(ro, head="renamed-locally")

        assert ro._with_local_diff(pr, local).head == "renamed-locally"

    def test_pr_narrative_is_preserved(self, ro):
        pr = _pr_metadata(ro, labels=["review"], author="isaac", is_draft=True)
        local = _pr_metadata(ro, title="add unpushed", body="", head_sha="b" * 40)

        merged = ro._with_local_diff(pr, local)

        assert merged.title == "feat: thing"
        assert merged.body == "why"
        assert merged.labels == ["review"]
        assert merged.author == "isaac"
        assert merged.is_draft is True

    def test_drift_is_reported(self, ro, capsys):
        pr = _pr_metadata(ro)
        local = _pr_metadata(ro, head_sha="b" * 40)

        ro._with_local_diff(pr, local)

        err = capsys.readouterr().err
        assert "b" * 7 in err
        assert "a" * 7 in err

    def test_matching_heads_are_silent(self, ro, capsys):
        pr = _pr_metadata(ro)

        ro._with_local_diff(pr, _pr_metadata(ro))

        assert capsys.readouterr().err == ""


class TestFetchMetadataSelfMode:
    def test_self_review_of_a_pr_sees_unpushed_commits(self, ro, tmp_path, monkeypatch):
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "unpushed.go").write_text("package main\nfunc unpushed() {}\n")
        commit_all(repo, "add unpushed")

        monkeypatch.setattr(
            review.pipeline, "fetch_pr_metadata",
            lambda repo_name, pr_number: _pr_metadata(ro),
        )

        run_ctx = ro.fetch_metadata("o/r", "1", ro.Mode.SELF, str(repo))
        pr, ctx, pr_data = run_ctx.pr, run_ctx.context, run_ctx.data

        assert [f["path"] for f in pr.files] == ["unpushed.go"]
        assert pr.head_sha != "a" * 40
        assert pr.title == "feat: thing"
        assert pr_data is None

    def test_an_explicit_base_outranks_the_one_github_reports(
        self, ro, tmp_path, monkeypatch,
    ):
        """The caller resolved a base through the shared ladder and the two
        disagree. The caller's wins: it is the one the supersession gate already
        measured against, and a run whose gate and diff name different bases
        refuses over commits it then declines to review."""
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "develop", "-q")
        (repo / "dev.go").write_text("package main\n")
        commit_all(repo, "add dev")
        git_out(repo, "fetch", "-q", "origin", "develop")
        git_out(repo, "checkout", "-b", "feat", "-q")
        (repo / "feat.go").write_text("package main\n")
        commit_all(repo, "add feat")

        monkeypatch.setattr(
            review.pipeline, "fetch_pr_metadata",
            lambda repo_name, pr_number: _pr_metadata(ro, base="main"),
        )

        run_ctx = ro.fetch_metadata(
            "o/r", "1", ro.Mode.SELF, str(repo), base="develop",
        )

        assert run_ctx.pr.base == "develop"
        assert [f["path"] for f in run_ctx.pr.files] == ["feat.go"]

    def test_an_explicit_base_reaches_a_self_review_with_no_pr(
        self, ro, tmp_path,
    ):
        """The stacked branch that has no PR yet: nothing upstream states a
        base, so `--base` is the only thing that can name the parent."""
        repo = init_repo(tmp_path / "repo")
        (repo / "main.go").write_text("package main\n")
        commit_all(repo, "init")
        add_self_origin(repo)
        git_out(repo, "checkout", "-b", "parent", "-q")
        (repo / "parent.go").write_text("package main\n")
        commit_all(repo, "add parent")
        git_out(repo, "fetch", "-q", "origin", "parent")
        git_out(repo, "checkout", "-b", "child", "-q")
        (repo / "child.go").write_text("package main\n")
        commit_all(repo, "add child")

        run_ctx = ro.fetch_metadata(
            "o/r", "", ro.Mode.SELF, str(repo), base="parent",
        )

        assert run_ctx.pr.base == "parent"
        assert [f["path"] for f in run_ctx.pr.files] == ["child.go"]


class TestSingleAgentIngestRejection:
    def test_rejected_single_agent_output_exits(self, ro, tmp_path, monkeypatch, capsys):
        """BANNER in the agent's review.md is not a review: log and exit 1."""
        from review.render import BANNER

        job = _make_job(PreflightData(
            diff="", commit_log="", file_contents={},
            file_permissions={}, instructions_md="", architecture_md="",
        ))
        job.review_file = str(tmp_path / "review.md")
        job.session_log = str(tmp_path / "session.jsonl")
        job.wt_path = str(tmp_path)

        def _agent(invocation, throttle=None) -> int:
            Path(invocation.session_log).write_text("{}")
            Path(job.review_file).write_text(
                f"{BANNER}\n\n## Summary\nLooks good.\n\n## Verdict\nApprove.\n"
            )
            return 0

        monkeypatch.setattr(review.pipeline, "build_prompt", lambda *a, **k: "PROMPT")
        monkeypatch.setattr(review.phases, "run_agent", _agent)
        with pytest.raises(SystemExit) as exc:
            review.pipeline.run_single_agent(job, disprove=False)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "rejected" in err.lower()


# ── merge cap ─────────────────────────────────────────────────────────


class TestGroupMergeCap:
    """A merged group's diff is capped at what its prompt has room for."""

    def _job(self, preflight):
        return _make_job(preflight)

    def _patch(self, monkeypatch, target):
        monkeypatch.setattr(review.pipeline, "phase_model", lambda *a, **k: "m")
        monkeypatch.setattr(
            review.pipeline, "ladder_target_bytes", lambda *a, **k: target,
        )

    def test_the_fixed_context_and_template_are_reserved(self, monkeypatch):
        self._patch(monkeypatch, 300_000)
        pf = PreflightData(
            diff="", commit_log="x" * 40_000, file_contents={},
            file_permissions={}, instructions_md="c" * 10_000,
            architecture_md="a" * 5_000,
        )
        cap = review.pipeline._group_merge_cap(self._job(pf))
        # The commit log is a lever, so it is not charged against the cap.
        fixed = fixed_preflight_bytes(
            pf.instructions_md, pf.architecture_md, pf.review_checklists,
            pf.review_profiles,
        )
        assert fixed >= 15_000  # the context files are charged at least whole
        assert cap == (
            300_000 - TEMPLATE_OVERHEAD_BYTES - fixed
            - MAX_DERIVED_FOCUS * DERIVED_AREA_RESERVE_BYTES
        )

    def test_never_below_the_diff_floor(self, monkeypatch):
        self._patch(monkeypatch, 30_000)
        pf = PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="c" * 50_000, architecture_md="",
        )
        assert review.pipeline._group_merge_cap(self._job(pf)) == MIN_DIFF_BYTES

    def test_the_merge_cap_reserves_the_focus_and_issue_sections(self, monkeypatch):
        self._patch(monkeypatch, 300_000)
        pf = PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
        )
        job = self._job(pf)
        without = review.pipeline._group_merge_cap(job)
        job.issue_context = "x" * (6 * 1024)
        with_issue = review.pipeline._group_merge_cap(job)
        assert without - with_issue >= 6 * 1024

    def test_the_merge_cap_reserves_room_for_phase_one_areas(self, monkeypatch):
        self._patch(monkeypatch, 300_000)
        pf = PreflightData(
            diff="", commit_log="", file_contents={}, file_permissions={},
            instructions_md="", architecture_md="",
        )
        requested = user_area("Auth", "why")
        derived = area(id="F2", title="Auth", rationale="why")
        job_need = self._job(pf)
        job_need.focus = (requested,)
        job_held = self._job(pf)
        job_held.focus = (requested, derived)
        reserve = MAX_DERIVED_FOCUS * DERIVED_AREA_RESERVE_BYTES
        extra = (
            len(_build_focus_section(job_held.focus).encode())
            - len(_build_focus_section(job_need.focus).encode())
        )
        cap_need = review.pipeline._group_merge_cap(job_need)
        cap_held = review.pipeline._group_merge_cap(job_held)
        assert cap_need == cap_held - reserve + extra
        job_more = self._job(pf)
        job_more.focus = (requested, derived, area(id="F3", title="Auth", rationale="why"))
        extra_more = (
            len(_build_focus_section(job_more.focus).encode())
            - len(_build_focus_section(job_held.focus).encode())
        )
        assert review.pipeline._group_merge_cap(job_more) == cap_held - extra_more
