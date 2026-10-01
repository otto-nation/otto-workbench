"""What a failed conflict resolution is told on its second attempt.

`resolve_chunked` used to hand the retry a predicate that threw the parser's
failure reason away, so every failure fell back to the generic marker wording
— a lecture about emitting markers, sent to an answer whose markers were
perfect and whose mistake was repeating the context back.
`retry_blank_response`'s own docstring warns against exactly that, and these
pin that the warning is now heeded.
"""

import sys
from pathlib import Path
from unittest import mock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from agent import retry as agent_retry  # noqa: E402
from rebase import conflicts as rebase_conflicts  # noqa: E402
from rebase import resolve_ai as rebase_resolve  # noqa: E402

_CONFLICT = "<<<<<<< HEAD\n    return a\n=======\n    return b\n>>>>>>> abc\n"
_AFTER = "\n# next thing\ndef other():\n"


class TestHintForReason:
    @pytest.mark.parametrize("reason,expected", [
        ("echoed_context_in_block_2:3", agent_retry.ECHOED_CONTEXT_HINT),
        ("wholly_echoed_context_in_block_1", agent_retry.ECHOED_CONTEXT_HINT),
        ("surviving_conflict_marker_in_block_1:<<<<<<<",
         agent_retry.SURVIVING_MARKER_HINT),
        ("surviving_conflict_marker:<<<<<<<", agent_retry.SURVIVING_MARKER_HINT),
        ("missing_markers_for_block_2", agent_retry.BLANK_RESPONSE_HINT),
        ("missing_both_markers", agent_retry.BLANK_RESPONSE_HINT),
        ("", agent_retry.BLANK_RESPONSE_HINT),
    ])
    def test_each_failure_earns_its_own_correction(self, reason, expected):
        assert rebase_resolve.hint_for_reason(reason) is expected

    def test_the_echo_hint_does_not_lecture_about_markers(self):
        """The precise mistake this used to make, as an assertion.

        The generic hint's instruction is to emit the markers; sending it to a
        resolution whose markers were faultless corrects nothing and buys a
        second identical answer.
        """
        hint = rebase_resolve.hint_for_reason("echoed_context_in_block_1:2")
        assert "context" in hint
        assert hint is not agent_retry.BLANK_RESPONSE_HINT

    def test_an_unknown_failure_falls_back_rather_than_borrowing(self):
        """A new failure mode must not silently inherit another's correction."""
        assert rebase_resolve.hint_for_reason(
            "some_future_failure_in_block_1",
        ) is agent_retry.BLANK_RESPONSE_HINT


class TestTheRetryIsToldWhatWentWrong:
    def _answers(self, first: str, second: str):
        """A backend that returns *first*, then *second*, recording prompts."""
        seen = []

        def call(prompt: str):
            seen.append(prompt)
            return (first, 0) if len(seen) == 1 else (second, 0)

        return call, seen

    def test_a_callable_hint_sees_the_unusable_answer(self):
        call, seen = self._answers("nonsense", "also nonsense")
        agent_retry.retry_blank_response(
            call, "PROMPT", label="x", usable=lambda s: False,
            hint=lambda answer: f"YOU SAID {answer!r}. ",
        )

        assert seen[1].startswith("YOU SAID 'nonsense'. ")

    def test_a_plain_string_hint_still_works(self):
        call, seen = self._answers("nonsense", "still nonsense")
        agent_retry.retry_blank_response(
            call, "PROMPT", label="x", usable=lambda s: False, hint="FIXED HINT. ",
        )

        assert seen[1].startswith("FIXED HINT. ")

    def test_no_hint_is_resolved_when_the_first_answer_was_usable(self):
        """The hint callable must not be asked about an answer that parsed."""
        calls = []

        def hint(answer):
            calls.append(answer)
            return ""

        agent_retry.retry_blank_response(
            lambda prompt: ("fine", 0), "PROMPT", label="x",
            usable=lambda s: True, hint=hint,
        )

        assert calls == []


class TestChunkedResolutionFallsBackToTheWholeFile:
    """A chunked failure is not the end of the file's chances.

    The failures that get here are about the chunked format itself — a block's
    markers missing, or context echoed around the answer — and neither exists
    in a prompt that asks for the file entire. The chunked path writes nothing
    until every block has parsed, so the file is untouched and the second
    prompt is a live option rather than a repeat.
    """

    def _file(self, tmp_path) -> Path:
        body = "".join(f"line {i}\n" for i in range(300))
        path = tmp_path / "big.py"
        path.write_text(body + _CONFLICT + body)
        return path

    def test_the_whole_file_prompt_runs_after_a_chunked_failure(self, tmp_path):
        path = self._file(tmp_path)

        with mock.patch.object(rebase_resolve, "resolve_chunked",
                               return_value=None) as chunked, \
             mock.patch.object(rebase_resolve, "resolve_full_file",
                               return_value="big.py") as whole:
            result = rebase_resolve.resolve_single_file(
                "big.py", path, "abc123", "feat: x", str(tmp_path),
                target_ref="origin/main",
            )

        chunked.assert_called_once()
        whole.assert_called_once()
        assert result == "big.py"

    def test_a_successful_chunked_resolution_does_not_fall_back(self, tmp_path):
        path = self._file(tmp_path)

        with mock.patch.object(rebase_resolve, "resolve_chunked",
                               return_value="big.py"), \
             mock.patch.object(rebase_resolve, "resolve_full_file") as whole:
            rebase_resolve.resolve_single_file(
                "big.py", path, "abc123", "feat: x", str(tmp_path),
                target_ref="origin/main",
            )

        whole.assert_not_called()

    def test_the_file_is_unchanged_when_the_chunked_path_fails(self, tmp_path):
        """What makes the fallback safe rather than a second write."""
        path = self._file(tmp_path)
        before = path.read_text()

        with mock.patch.object(rebase_resolve.agent_invoke, "run_prompt",
                               return_value=mock.Mock(exit_code=0, text="nonsense")), \
             mock.patch.object(rebase_conflicts, "get_commit_diff", return_value=None):
            blocks = rebase_conflicts.extract_conflict_blocks(before)
            assert rebase_resolve.resolve_chunked(
                "big.py", path, before, blocks, "abc123", "feat: x",
                str(tmp_path), target_ref="origin/main",
            ) is None

        assert path.read_text() == before
