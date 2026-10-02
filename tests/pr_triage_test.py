"""pr.triage: JSON extraction, the thrash guard and verdict downgrades."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary  # noqa: E402
import agent.retry
import pr.thread_context
import pr.triage
from pr.thread_models import CommentItem, PRReport, ReportThread, Verification
import agent.backend


# ── triage.extract_json ───────────────────────────────────────────────────────────

class TestExtractJson:
    def test_plain_json(self):
        assert pr.triage.extract_json('{"a": 1}') == '{"a": 1}'

    def test_json_fenced(self):
        text = '```json\n{"a": 1}\n```'
        assert pr.triage.extract_json(text) == '{"a": 1}'

    def test_bare_fence(self):
        text = '```\n{"a": 1}\n```'
        assert pr.triage.extract_json(text) == '{"a": 1}'

    def test_fence_with_surrounding_text(self):
        text = 'Here is the result:\n```json\n{"a": 1}\n```\nDone.'
        assert pr.triage.extract_json(text) == '{"a": 1}'

    def test_whitespace_stripped(self):
        assert pr.triage.extract_json('  {"a": 1}  ') == '{"a": 1}'

    def test_multiline_json_in_fence(self):
        text = '```json\n{\n  "threads": [],\n  "stats": {}\n}\n```'
        result = json.loads(pr.triage.extract_json(text))
        assert result == {"threads": [], "stats": {}}

    def test_preamble_before_bare_json(self):
        text = 'Here is the classification:\n{"a": 1}'
        result = json.loads(pr.triage.extract_json(text))
        assert result == {"a": 1}

    def test_preamble_and_trailing_text(self):
        text = 'Sure, here you go:\n{"threads": [], "stats": {}}\nHope this helps!'
        result = json.loads(pr.triage.extract_json(text))
        assert result == {"threads": [], "stats": {}}

    def test_multiline_preamble_before_json(self):
        text = 'I analyzed the threads.\nHere are the results:\n{\n  "a": 1\n}'
        result = json.loads(pr.triage.extract_json(text))
        assert result == {"a": 1}


# ── shared thrash guard wiring ──────────────────────────────────────────────


class TestTriageThrashGuard:
    """Triage has no session log — an unparseable answer is the only signal."""

    def test_parses_as_json_accepts_a_fenced_object(self):
        assert pr.triage.parses_as_json("```json\n{\"threads\": []}\n```")

    def test_parses_as_json_rejects_prose(self):
        assert not pr.triage.parses_as_json("I was unable to complete the triage.")

    def test_parses_as_json_rejects_a_json_object_of_the_wrong_shape(self):
        # Observed live: the agent narrated the tool call it wanted to make and
        # emitted a JSON object for *that*. extract_json slices first-brace to
        # last-brace, so it parsed, the retry never fired, and
        # triage_result_from_dict read the absent "threads" key as [] —
        # reporting nothing to triage on a PR full of unaddressed feedback.
        reply = (
            "Given the instructions, I should check for relevant skills first.\n\n"
            '{"cmd": "cat -n lib/registries.sh", "description": "Inspect reg_load"}'
        )
        assert not pr.triage.parses_as_json(reply)

    def test_parses_as_json_rejects_a_bare_non_object(self):
        assert not pr.triage.parses_as_json("[]")

    def test_parses_as_json_accepts_comment_items_only(self):
        # The other half of the contract: a run with no threads but decomposed
        # comment items is a legitimate answer, not a malformed one.
        assert pr.triage.parses_as_json('{"comment_items": []}')

    def test_parses_as_json_rejects_threads_key_of_the_wrong_type(self):
        # Key membership alone would accept this: a narrated tool call or a
        # quoted schema can carry a field literally named "threads" that isn't
        # a list. Requiring the value's shape closes that window.
        assert not pr.triage.parses_as_json('{"threads": "see above"}')

    def test_parses_as_json_accepts_an_explicit_null_beside_a_real_list(self):
        # `.get(key, fallback)` falls back only on a MISSING key, so writing
        # this as `parsed.get("threads", parsed.get("comment_items"))` rejects
        # a reply whose threads key is an explicit null even when the items
        # key holds a real list. `_lenient_list` exists precisely because the
        # model "emits the key with an explicit null often enough", so that is
        # a shape the consumer handles and this predicate must not refuse.
        assert pr.triage.parses_as_json('{"threads": null, "comment_items": []}')
        assert pr.triage.parses_as_json('{"threads": [], "comment_items": null}')

    def test_parses_as_json_rejects_both_keys_null(self):
        assert not pr.triage.parses_as_json('{"threads": null, "comment_items": null}')

    def test_unparseable_triage_output_earns_one_retry(self, tmp_path):
        report = PRReport(threads=[ReportThread(id="t1", reviewer="kgn")])
        prompts = []

        def prompt(text, **kw):
            prompts.append(text)
            return ("not json", 0) if len(prompts) == 1 else ('{"threads": []}', 0)

        with (
            patch.object(agent.backend, "prompt", side_effect=prompt),
            patch.object(pr.thread_context, "branch_commit_log", return_value=""),
        ):
            result, rc = pr.triage.run_triage(report, tmp_path, {})

        assert rc == 0
        assert result is not None
        assert len(prompts) == 2
        assert prompts[1].startswith(agent.retry.JSON_RESPONSE_HINT)

    def test_the_retry_corrects_json_rather_than_markers(self, tmp_path):
        """The marker hint named a format this prompt never asks for.

        Triage requests a bare JSON object. Told to "emit the requested
        markers", a second attempt is being corrected about something it was
        never asked to do, and fails the way the first did.
        """
        report = PRReport(threads=[ReportThread(id="t1", reviewer="kgn")])
        prompts = []

        def prompt(text, **kw):
            prompts.append(text)
            return ("I'll read the file first.", 0) if len(prompts) == 1 else (
                '{"threads": []}', 0)

        with (
            patch.object(agent.backend, "prompt", side_effect=prompt),
            patch.object(pr.thread_context, "branch_commit_log", return_value=""),
        ):
            pr.triage.run_triage(report, tmp_path, {})

        assert "JSON" in prompts[1]
        assert "markers" not in prompts[1].replace(prompts[0], "")

    def test_non_json_triage_output_is_kept_whole(self, tmp_path):
        """The old record kept a 500-character preview and no way to the rest."""
        trail = MagicMock()
        report = PRReport(threads=[ReportThread(id="t1", reviewer="kgn")])

        def prompt(text, **kw):
            return ("sorry, I cannot do that", 0)

        with (
            patch.object(agent.backend, "prompt", side_effect=prompt),
            patch.object(pr.thread_context, "branch_commit_log", return_value=""),
        ):
            result, rc = pr.triage.run_triage(report, tmp_path, {}, trail)

        assert result is None
        assert rc == 1
        assert trail.failure.call_args.kwargs["output"] == "sorry, I cannot do that"
        assert "output_preview" not in trail.failure.call_args.kwargs.get("data", {})


# ── permalink-backed claims ─────────────────────────────────────────────────


class TestUnsupportedVerdictDowngrade:
    """A verdict posted to a reviewer is a claim; a claim needs a line."""

    def _item(self, **kw):
        kw.setdefault("verification", "invalid")
        return CommentItem(id="t1", summary="s", **kw)

    def test_uncited_invalid_becomes_needs_discussion(self, tmp_path):
        item = self._item(complexity="low")
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"
        assert item.complexity == ""

    def test_uncited_already_addressed_becomes_needs_discussion(self, tmp_path):
        item = self._item(verification="already_addressed")
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_every_evidence_bearing_verdict_is_downgraded(self, tmp_path):
        """Driven by the enum, so a new citing verdict is covered on arrival."""
        citing = [m for m in Verification if m.needs_evidence]
        assert citing, "no verdict claims to need evidence"
        for member in citing:
            entry = CommentItem(id="t1", verification=member)
            downgraded = pr.triage.downgrade_unsupported_verdicts([entry], tmp_path)
            assert downgraded == 1, f"{member} not downgraded"
            assert entry.verification is Verification.NEEDS_DISCUSSION

    def test_reason_is_recorded_so_the_author_knows_why(self, tmp_path):
        item = self._item(reasoning="reviewer misread the guard")
        pr.triage.downgrade_unsupported_verdicts([item], tmp_path)
        assert "reviewer misread the guard" in item.reasoning
        assert "cited no line" in item.reasoning
        assert "downgraded from invalid" in item.reasoning

    def test_cited_verdict_that_exists_in_the_tree_survives(self, tmp_path):
        (tmp_path / "app.py").write_text("x = 1\n")
        item = self._item(evidence_file="app.py", evidence_line=1)
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 0
        assert item.verification == "invalid"

    def test_citation_to_a_file_that_does_not_exist_is_downgraded(self, tmp_path):
        """A link to nothing is no better than no link."""
        item = self._item(evidence_file="ghost.py", evidence_line=3)
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_valid_verdicts_are_left_alone(self, tmp_path):
        item = self._item(verification="valid", complexity="low")
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 0
        assert item.complexity == "low"

    def test_an_absolute_citation_outside_the_repo_is_downgraded(self, tmp_path):
        """Joining a repo dir with an absolute path discards the repo dir."""
        # Use a name unique to this test's tmp_path to avoid colliding with the
        # traversal test when both run in the same session directory.
        outside = tmp_path.parent / f"outside_abs_{tmp_path.name}.py"
        outside.write_text("secret = 1\n")
        item = self._item(evidence_file=str(outside), evidence_line=1)
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_a_traversal_out_of_the_repo_is_downgraded(self, tmp_path):
        """`..` reaching a file that really exists still is not this repo's code."""
        # Use a name unique to this test's tmp_path to avoid colliding with the
        # absolute-citation test when both run in the same session directory.
        outside_name = f"outside_trav_{tmp_path.name}.py"
        (tmp_path.parent / outside_name).write_text("secret = 1\n")
        item = self._item(evidence_file=f"../{outside_name}", evidence_line=1)
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_a_citation_past_the_end_of_the_file_is_downgraded(self, tmp_path):
        """A permalink to a line the file does not have highlights nothing."""
        (tmp_path / "app.py").write_text("x = 1\n")
        item = self._item(evidence_file="app.py", evidence_line=99)
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 1
        assert item.verification == "needs_discussion"

    def test_the_last_line_of_a_file_is_still_inside_it(self, tmp_path):
        (tmp_path / "app.py").write_text("a\nb\nc\n")
        item = self._item(evidence_file="app.py", evidence_line=3)
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 0

    def test_a_nested_citation_inside_the_repo_survives(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        (tmp_path / "pkg" / "mod.py").write_text("x = 1\n")
        item = self._item(evidence_file="pkg/mod.py", evidence_line=1)
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 0

    def test_a_citation_to_a_directory_is_downgraded(self, tmp_path):
        (tmp_path / "pkg").mkdir()
        item = self._item(evidence_file="pkg", evidence_line=1)
        assert pr.triage.downgrade_unsupported_verdicts([item], tmp_path) == 1
