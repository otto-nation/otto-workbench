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

import agent.retry  # noqa: E402
import rebase.conflicts  # noqa: E402
import rebase.resolve_ai  # noqa: E402
import rebase.survival  # noqa: E402
import rebase.types  # noqa: E402
import agent.invoke

_CONFLICT = "<<<<<<< HEAD\n    return a\n=======\n    return b\n>>>>>>> abc\n"
_AFTER = "\n# next thing\ndef other():\n"


def _block() -> rebase.types.ConflictBlock:
    """One conflict with context on both sides, as the chunked prompt sends it."""
    return rebase.types.ConflictBlock(
        index=1, start=0, end=4, conflict=_CONFLICT,
        context_before="def f(a, b):\n", context_after=_AFTER,
    )


class TestHintForReason:
    @pytest.mark.parametrize("reason,expected", [
        ("wholly_echoed_context_in_block_1", agent.retry.ECHOED_CONTEXT_HINT),
        ("wholly_echoed_context", agent.retry.ECHOED_CONTEXT_HINT),
        ("surviving_conflict_marker_in_block_1:<<<<<<<",
         agent.retry.SURVIVING_MARKER_HINT),
        ("surviving_conflict_marker:<<<<<<<", agent.retry.SURVIVING_MARKER_HINT),
        ("missing_markers_for_block_2", agent.retry.BLANK_RESPONSE_HINT),
        ("missing_both_markers", agent.retry.BLANK_RESPONSE_HINT),
        ("does_not_parse", agent.retry.DOES_NOT_PARSE_HINT),
        ("", agent.retry.BLANK_RESPONSE_HINT),
    ])
    def test_each_failure_earns_its_own_correction(self, reason, expected):
        assert rebase.resolve_ai.hint_for_reason(reason) is expected

    def test_the_echo_hint_does_not_lecture_about_markers(self):
        """The precise mistake this used to make, as an assertion.

        The generic hint's instruction is to emit the markers; sending it to a
        resolution whose markers were faultless corrects nothing and buys a
        second identical answer.

        Phrased against the reason a parser really emits. An ordinary echo is
        trimmed rather than reported, so the wholly-echoed case is the only
        echo that reaches a retry at all — asserting on a reason string
        nothing produces would pin the dispatch table to itself.
        """
        hint = rebase.resolve_ai.hint_for_reason("wholly_echoed_context_in_block_1")
        assert "context" in hint
        assert hint is not agent.retry.BLANK_RESPONSE_HINT

    def test_every_hint_key_is_a_reason_some_parser_emits(self):
        """The table must not accumulate vocabulary nothing can produce.

        A key no parser emits reads as a live failure mode that never fires,
        and the test pinning it reads as coverage. Both are worse than the
        missing entry would be — so the source of truth is what the parsers
        actually write, and this is what keeps the two in step.
        """
        emitted = set()
        for block_count, text in (
            (1, "nothing parseable here"),
            (1, f"{rebase.conflicts.RESOLVE_BEGIN}_1\n<<<<<<< HEAD\n"
                f"{rebase.conflicts.RESOLVE_END}_1\n"),
            (1, f"{rebase.conflicts.RESOLVE_BEGIN}_1\n{_AFTER}"
                f"{rebase.conflicts.RESOLVE_END}_1\n"),
        ):
            parsed = rebase.conflicts.parse_chunked_resolutions(
                text, [_block() for _ in range(block_count)],
            )
            if parsed.reason:
                emitted.add(parsed.reason.split(":", 1)[0]
                            .split("_in_block_", 1)[0])
        for text in ("no markers",
                     f"{rebase.conflicts.RESOLVE_BEGIN}\n<<<<<<< HEAD\n"
                     f"{rebase.conflicts.RESOLVE_END}"):
            reason = rebase.conflicts.parse_resolved_content(text)[1]
            if reason:
                emitted.add(reason.split(":", 1)[0])
        syntax = rebase.resolve_ai.judge_answer(
            "mod.py",
            rebase.conflicts.StageTexts(
                base="x = 1\n", target="x = 1\n", replayed="x = 2\n",
            ),
            f"{rebase.conflicts.RESOLVE_BEGIN}\ndef f(\n"
            f"{rebase.conflicts.RESOLVE_END}",
        )
        if syntax.reason:
            emitted.add(syntax.reason.split(":", 1)[0])

        for failure in rebase.resolve_ai._HINT_FOR_FAILURE:
            assert any(e == failure.value or e.startswith(f"{failure.value}_")
                       for e in emitted), f"{failure.value} is emitted by no parser"

    def test_an_unknown_failure_falls_back_rather_than_borrowing(self):
        """A new failure mode must not silently inherit another's correction."""
        assert rebase.resolve_ai.hint_for_reason(
            "some_future_failure_in_block_1",
        ) is agent.retry.BLANK_RESPONSE_HINT

    def test_a_syntax_failure_hint_includes_the_parser_detail(self):
        hint = rebase.resolve_ai.hint_for_reason(
            "does_not_parse:expected declaration, found '}'",
        )
        assert hint.startswith(agent.retry.DOES_NOT_PARSE_HINT)
        assert "expected declaration, found '}'" in hint


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
        agent.retry.retry_blank_response(
            call, "PROMPT", label="x", usable=lambda s: False,
            hint=lambda answer: f"YOU SAID {answer!r}. ",
        )

        assert seen[1].startswith("YOU SAID 'nonsense'. ")

    def test_a_plain_string_hint_still_works(self):
        call, seen = self._answers("nonsense", "still nonsense")
        agent.retry.retry_blank_response(
            call, "PROMPT", label="x", usable=lambda s: False, hint="FIXED HINT. ",
        )

        assert seen[1].startswith("FIXED HINT. ")

    def test_no_hint_is_resolved_when_the_first_answer_was_usable(self):
        """The hint callable must not be asked about an answer that parsed."""
        calls = []

        def hint(answer):
            calls.append(answer)
            return ""

        agent.retry.retry_blank_response(
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

        with mock.patch.object(rebase.resolve_ai, "resolve_chunked",
                               return_value=None) as chunked, \
             mock.patch.object(rebase.resolve_ai, "resolve_full_file",
                               return_value="big.py") as whole:
            result = rebase.resolve_ai.resolve_single_file(
                "big.py", path, "abc123", "feat: x", str(tmp_path),
                target_ref="origin/main",
            )

        chunked.assert_called_once()
        whole.assert_called_once()
        assert result == "big.py"

    def test_a_successful_chunked_resolution_does_not_fall_back(self, tmp_path):
        path = self._file(tmp_path)

        with mock.patch.object(rebase.resolve_ai, "resolve_chunked",
                               return_value="big.py"), \
             mock.patch.object(rebase.resolve_ai, "resolve_full_file") as whole:
            rebase.resolve_ai.resolve_single_file(
                "big.py", path, "abc123", "feat: x", str(tmp_path),
                target_ref="origin/main",
            )

        whole.assert_not_called()

    def test_the_file_is_unchanged_when_the_chunked_path_fails(self, tmp_path):
        """What makes the fallback safe rather than a second write."""
        path = self._file(tmp_path)
        before = path.read_text()

        with mock.patch.object(agent.invoke, "run_prompt",
                               return_value=mock.Mock(exit_code=0, text="nonsense")), \
             mock.patch.object(rebase.conflicts, "get_commit_diff", return_value=None):
            blocks = rebase.conflicts.extract_conflict_blocks(before)
            assert rebase.resolve_ai.resolve_chunked(
                "big.py", path, before, blocks, "abc123", "feat: x",
                str(tmp_path), target_ref="origin/main",
            ) is None

        assert path.read_text() == before


class TestAWholeFileAnswerMustKeepTheCleanChanges:
    """A whole-file answer that copies one side through parses perfectly.

    That is the AI's version of `git checkout --ours`, and the commit hook
    would refuse it after the fact. Checked before the write instead, so the
    retry is spent on the mistake and a file is never staged with work missing.
    """

    _STAGES = rebase.conflicts.StageTexts(
        base="a\nb\nc\nd\ne\nf\ng\n",
        target="a\nBM\nc\nD\ne\nf\ng\n",
        replayed="a\nB\nc\nd\ne\nF\ng\n",
    )

    @staticmethod
    def _answer(body: str) -> str:
        return f"{rebase.conflicts.RESOLVE_BEGIN}\n{body}{rebase.conflicts.RESOLVE_END}\n"

    def test_an_answer_that_is_the_target_verbatim_is_unusable(self):
        verdict = rebase.resolve_ai.judge_answer(
            "f.txt", self._STAGES, self._answer(self._STAGES.target),
        )

        assert not verdict.usable
        assert [loss.kind for loss in verdict.losses] == [
            rebase.survival.LossKind.FILE_TAKEN_WHOLE,
        ]

    def test_an_answer_keeping_both_sides_is_usable(self):
        verdict = rebase.resolve_ai.judge_answer(
            "f.txt", self._STAGES, self._answer("a\nBM+B\nc\nD\ne\nF\ng\n"),
        )

        assert verdict.usable and verdict.losses == ()

    def test_an_unparseable_answer_reports_the_parse_failure_not_losses(self):
        verdict = rebase.resolve_ai.judge_answer("f.txt", self._STAGES, "no markers at all")

        assert not verdict.usable
        assert verdict.losses == ()
        assert verdict.reason == rebase.types.ParseFailure.MISSING_BOTH_MARKERS

    def test_the_retry_hint_names_what_went_missing(self):
        losses = rebase.resolve_ai.judge_answer(
            "f.txt", self._STAGES, self._answer("a\nBM+B\nc\nD\ne\nf\ng\n"),
        ).losses

        hint = rebase.resolve_ai.dropped_change_hint(losses)

        assert hint.startswith(agent.retry.DROPPED_CHANGE_HINT)
        assert "line 6" in hint and "+ F" in hint

    def _resolve(self, tmp_path, answer_text, retried):
        path = tmp_path / "f.txt"
        path.write_text("conflicted\n")

        def fake_run_prompt(*args, usable, retry_hint, **kwargs):
            if not usable(answer_text):
                retried.append(retry_hint(answer_text))
            return mock.Mock(exit_code=0, text=answer_text)

        trail = mock.MagicMock()
        with mock.patch.object(rebase.conflicts, "stage_texts", return_value=self._STAGES), \
             mock.patch.object(rebase.conflicts, "get_commit_diff", return_value=""), \
             mock.patch.object(rebase.conflicts, "git_add", return_value=True), \
             mock.patch.object(agent.invoke, "run_prompt", side_effect=fake_run_prompt):
            result = rebase.resolve_ai.resolve_full_file(
                "f.txt", path, "conflicted\n", "abc123", "feat: x", str(tmp_path),
                target_ref="origin/main", trail=trail,
            )
        return result, path, trail

    def test_a_discarding_answer_is_retried_and_never_written(self, tmp_path):
        retried = []

        result, path, trail = self._resolve(
            tmp_path, self._answer(self._STAGES.target), retried,
        )

        assert result is None
        assert path.read_text() == "conflicted\n"
        assert len(retried) == 1 and retried[0].startswith(agent.retry.DROPPED_CHANGE_HINT)
        assert trail.failure.call_args.kwargs["data"]["filepath"] == "f.txt"

    def test_a_good_answer_is_written(self, tmp_path):
        retried = []

        result, path, _ = self._resolve(
            tmp_path, self._answer("a\nBM+B\nc\nD\ne\nF\ng\n"), retried,
        )

        assert result == "f.txt"
        assert path.read_text() == "a\nBM+B\nc\nD\ne\nF\ng\n"
        assert retried == []

    def test_the_same_answer_is_audited_once_not_three_times(self, tmp_path):
        """`usable`, `retry_hint`, and the post-prompt check share one verdict."""
        retried = []

        with mock.patch.object(
            rebase.survival, "audit", wraps=rebase.survival.audit,
        ) as audit:
            self._resolve(tmp_path, self._answer(self._STAGES.target), retried)

        assert audit.call_count == 1
