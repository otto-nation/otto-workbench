"""Tests for rebase.resolve_ai: the resolve prompts and single-file resolution."""

import subprocess
import sys
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import rebase.types  # noqa: E402
import rebase.conflicts  # noqa: E402
import rebase.resolve_ai  # noqa: E402
import agent.invoke  # noqa: E402
import agent.backend  # noqa: E402

from pr_rebase_support import _TARGET, _OTHER_TARGET, _make_large_file, _block


# ── _build_resolve_prompt ──────────────────────────────────────────────────


def test_build_resolve_prompt_includes_context():
    prompt = rebase.resolve_ai.build_resolve_prompt(
        "src/auth.py", "<<<<<<< HEAD\nbase\n=======\nbranch\n>>>>>>> abc123\n",
        "abc123", "fix: auth refresh", target_ref=_TARGET,
    )
    assert "src/auth.py" in prompt
    assert "abc123" in prompt
    assert "fix: auth refresh" in prompt
    assert "<<<RESOLVED>>>" in prompt
    assert "<<<END_RESOLVED>>>" in prompt
    assert "<<<<<<< HEAD" in prompt
    assert "BASE VERSION" not in prompt
    assert "COMMIT DIFF" not in prompt


def test_build_resolve_prompt_includes_ours_content():
    prompt = rebase.resolve_ai.build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh", target_ref=_TARGET,
        ours_content="base side content\n",
    )
    assert "--- BASE VERSION (target side before this commit) ---" in prompt
    assert "base side content" in prompt
    assert "--- END BASE VERSION ---" in prompt
    assert "base-side names" in prompt


def test_build_resolve_prompt_includes_commit_diff():
    diff = "--- a/src/auth.py\n+++ b/src/auth.py\n@@ -1 +1 @@\n-old\n+new"
    prompt = rebase.resolve_ai.build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh", target_ref=_TARGET,
        commit_diff=diff,
    )
    assert "--- COMMIT DIFF (what this commit intended to change) ---" in prompt
    assert diff in prompt
    assert "--- END COMMIT DIFF ---" in prompt


def test_build_resolve_prompt_includes_both_contexts():
    prompt = rebase.resolve_ai.build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh", target_ref=_TARGET,
        ours_content="base content\n",
        commit_diff="diff content",
    )
    assert "BASE VERSION" in prompt
    assert "COMMIT DIFF" in prompt
    assert "base-side names" in prompt


def test_both_prompts_bound_keep_both_with_the_duplicate_caveat():
    """"Keep both" must not be the last word on two sides adding one thing.

    Two commits that each add the same declaration at different offsets are
    non-overlapping additions by the letter of that instruction, so a resolver
    told only to keep both emits the declaration twice — which landed a
    repeated dataclass field and a repeated kwarg (a hard `SyntaxError`) in one
    rebase of this very branch. Both builders carry the caveat or neither is
    fixed: the chunked one runs on large files, which is where it happened.
    """
    full = rebase.resolve_ai.build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh", target_ref=_TARGET,
    )
    chunked = rebase.resolve_ai.build_chunked_prompt(
        "src/auth.py",
        [_block(conflict="<<<<<<< HEAD\na\n=======\nb\n>>>>>>> abc123\n")],
        "abc123", "fix: auth refresh", target_ref=_TARGET,
    )
    for prompt in (full, chunked):
        assert "keep both" in prompt
        keep_both = prompt.index("keep both")
        caveat = prompt.find("declares each thing", keep_both)
        assert caveat != -1, "keep-both instruction carries no duplicate caveat"
        assert caveat - keep_both < 400, "caveat too far from the instruction to bind it"


def test_build_resolve_prompt_names_the_resolved_ref():
    """The prompt tells the model which branch the commit is being replayed onto."""
    prompt = rebase.resolve_ai.build_resolve_prompt(
        "src/auth.py", "conflict content",
        "abc123", "fix: auth refresh", target_ref=_OTHER_TARGET,
    )
    assert _OTHER_TARGET in prompt
    assert "origin/main" not in prompt


class TestLedgerAttribution:
    """A rebase-assist call bills to the PR the run is rebasing.

    The helpers making these calls are several frames below the resolved
    context, so they read the subject off the run's trail. A call that reaches
    the ledger with neither repo nor PR cannot be attributed afterwards.
    """

    @staticmethod
    def _trail_for(**context):
        trail = mock.MagicMock()
        trail.context = dict(context)
        return trail

    @staticmethod
    def _resolving(tmp_path, trail, recorded):
        """Drive one rebase-assist prompt and capture what it billed to."""
        with mock.patch.object(rebase.conflicts, "stage_texts",
                               return_value=rebase.conflicts.StageTexts(None, "", None)), \
             mock.patch.object(rebase.conflicts, "get_commit_diff", return_value=""), \
             mock.patch.object(agent.backend, "prompt",
                               side_effect=lambda *a, **kw: (
                                   recorded.update(kw) or ("", 1))):
            rebase.resolve_ai.resolve_full_file(
                "a.py", tmp_path / "a.py", "<<<<<<< ours\n", "1a2b3c4d",
                "subject", str(tmp_path), target_ref="origin/main", trail=trail,
            )

    def test_the_runs_repo_and_pr_reach_the_ledger(self, tmp_path):
        recorded = {}
        self._resolving(
            tmp_path, self._trail_for(repo="org/repo", pr=7, branch="feat/x"),
            recorded,
        )

        assert (recorded["repo"], recorded["pr"]) == ("org/repo", "7")

    def test_a_branch_with_no_pr_bills_to_the_repo_alone(self, tmp_path):
        recorded = {}
        self._resolving(
            tmp_path, self._trail_for(repo="org/repo", pr=None, branch="feat/x"),
            recorded,
        )

        assert (recorded["repo"], recorded["pr"]) == ("org/repo", None)


class TestFailureRecording:
    def test_an_unparseable_resolution_hands_over_the_whole_answer(self):
        """The old record kept 500 characters of a tail and no way to the rest."""
        fake_trail = mock.MagicMock()
        answer = mock.Mock(exit_code=0, text="the model explained itself at length")
        with mock.patch.object(rebase.conflicts, "stage_texts",
                               return_value=rebase.conflicts.StageTexts(None, "", None)), \
             mock.patch.object(rebase.conflicts, "get_commit_diff", return_value=""), \
             mock.patch.object(agent.invoke, "run_prompt",
                               return_value=answer):
            resolved = rebase.resolve_ai.resolve_full_file(
                "a.py", Path("/tmp/a.py"), "<<<<<<< ours\n", "1a2b3c4d", "subject",
                "/tmp/wt", target_ref="origin/main", trail=fake_trail,
            )

        assert resolved is None
        kwargs = fake_trail.failure.call_args.kwargs
        assert kwargs["output"] == answer.text
        assert "stdout_tail" not in kwargs["data"]
        assert "stdout_len" not in kwargs["data"]

    def test_an_unparseable_chunked_resolution_hands_over_the_whole_answer(self):
        """Same guard as the full-file path, exercised through the chunked one."""
        fake_trail = mock.MagicMock()
        answer = mock.Mock(exit_code=0, text="the model explained itself at length")
        block = rebase.types.ConflictBlock(
            index=1, start=0, end=0, conflict="<<<<<<< ours\n",
            context_before="", context_after="",
        )
        with mock.patch.object(rebase.conflicts, "get_commit_diff", return_value=""), \
             mock.patch.object(agent.invoke, "run_prompt",
                               return_value=answer):
            resolved = rebase.resolve_ai.resolve_chunked(
                "a.py", Path("/tmp/a.py"), "<<<<<<< ours\n", [block], "1a2b3c4d",
                "subject", "/tmp/wt", target_ref="origin/main", trail=fake_trail,
            )

        assert resolved is None
        kwargs = fake_trail.failure.call_args.kwargs
        assert kwargs["output"] == answer.text
        assert kwargs["data"] == {
            "filepath": "a.py",
            "reason": f"{rebase.types.ParseFailure.MISSING_BLOCK_MARKERS}_1",
        }


def test_build_chunked_prompt_structure():
    blocks = [_block(
        index=1, start=50, end=54,
        conflict="<<<<<<< HEAD\nold\n=======\nnew\n>>>>>>> abc\n",
        context_before="before line\n",
        context_after="after line\n",
    )]
    prompt = rebase.resolve_ai.build_chunked_prompt(
        "main.go", blocks, "abc123", "feat: change",
        commit_diff="diff content", target_ref=_TARGET,
    )
    assert "--- CONFLICT 1 ---" in prompt
    assert "--- END CONFLICT 1 ---" in prompt
    assert "before line" in prompt
    assert "after line" in prompt
    assert "<<<RESOLVED>>>_1" in prompt
    assert "<<<END_RESOLVED>>>_1" in prompt
    assert "diff content" in prompt
    assert "BASE VERSION" not in prompt


def test_build_chunked_prompt_instructs_base_side_names():
    """Chunked prompts carry the rename guard the full-file prompt has.

    Any file over _CHUNKED_MIN_LINES with a small conflict takes this path, so
    dropping the instruction here disarmed it for the common case.
    """
    prompt = rebase.resolve_ai.build_chunked_prompt(
        "main.go", [_block(index=1, start=0, end=4)], "abc123", "feat: change",
        target_ref=_TARGET,
    )
    assert "base-side names" in prompt


def test_build_chunked_prompt_names_the_resolved_ref():
    prompt = rebase.resolve_ai.build_chunked_prompt(
        "main.go", [_block(index=1, start=0, end=4)], "abc123", "feat: change",
        target_ref=_OTHER_TARGET,
    )
    assert _OTHER_TARGET in prompt
    assert "origin/main" not in prompt


def test_resolve_single_file_uses_chunked_for_large_file(tmp_path):
    content = _make_large_file(500, [(100, "old", "new")])
    f = tmp_path / "big.go"
    f.write_text(content)

    resolved_output = "<<<RESOLVED>>>_1\nmerged\n<<<END_RESOLVED>>>_1\n"

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["claude", "-p", "--bare"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=resolved_output, stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = rebase.resolve_ai.resolve_single_file(
            "big.go", f, "abc123", "feat: update", str(tmp_path),
            target_ref=_TARGET,
        )

    assert result == "big.go"
    written = f.read_text()
    assert "merged" in written
    assert "<<<<<<< " not in written


def test_resolve_single_file_uses_full_for_small_file(tmp_path):
    content = "<<<<<<< HEAD\nold code\n=======\nnew code\n>>>>>>> abc123\n"
    f = tmp_path / "small.go"
    f.write_text(content)

    resolved_output = "<<<RESOLVED>>>\nmerged code\n<<<END_RESOLVED>>>\n"

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["claude", "-p", "--bare"]:
            return subprocess.CompletedProcess(
                args=cmd, returncode=0, stdout=resolved_output, stderr="",
            )
        return subprocess.CompletedProcess(args=cmd, returncode=0, stdout="", stderr="")

    with mock.patch("subprocess.run", side_effect=fake_run):
        result = rebase.resolve_ai.resolve_single_file(
            "small.go", f, "abc123", "feat: update", str(tmp_path),
            target_ref=_TARGET,
        )

    assert result == "small.go"
    assert "merged code" in f.read_text()
