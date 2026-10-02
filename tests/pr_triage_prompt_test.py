"""pr.triage_prompt: what the triage prompt states and its schema example."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

# `_no_published_summary` is autouse: imported so pytest applies it here,
# never referenced by name.
from review_threads_support import _no_published_summary  # noqa: E402
import pr.triage_prompt
from pr.thread_models import Complexity, Verification


class TestTriagePromptVerificationValues:
    """The prompt must define every verification value it asks for."""

    def test_defines_every_verification_value(self):
        """Iterating the enum, so a new verdict cannot be added unexplained."""
        prompt = pr.triage_prompt.build_triage_prompt([], "diff")
        for member in Verification:
            if member is Verification.UNSET:
                continue
            assert f"- {member.value}:" in prompt

    def test_every_verification_member_has_guidance(self):
        expected = {m for m in Verification if m is not Verification.UNSET}
        assert set(pr.triage_prompt.VERIFICATION_GUIDANCE) == expected

    def test_every_complexity_member_has_guidance(self):
        expected = {m for m in Complexity if m is not Complexity.UNSET}
        assert set(pr.triage_prompt.COMPLEXITY_GUIDANCE) == expected

    def test_steers_away_from_invalid_for_satisfied_code(self):
        prompt = pr.triage_prompt.build_triage_prompt([], "diff")
        assert "is NEVER invalid" in prompt

    def test_commit_log_included_when_present(self):
        prompt = pr.triage_prompt.build_triage_prompt(
            [], "diff", commit_log="abc1234 fix(logging): inject logger",
        )
        assert "abc1234 fix(logging): inject logger" in prompt
        assert "already_addressed, not invalid" in prompt

    def test_commit_log_omitted_when_empty(self):
        prompt = pr.triage_prompt.build_triage_prompt([], "diff", commit_log="")
        assert "Commits already made on this branch" not in prompt


class TestTriagePromptStatesItsOwnLimits:
    """What the prompt promises must match the shape it runs in.

    Triage is prompt-shaped: the backend is invoked with no tools at all. A
    prompt that requires a cited line as evidence, handed no code to cite,
    reads to a model as an instruction to go and read the file — and the reply
    is a narration of that read rather than the JSON the caller parses. Both
    halves are asserted here because either alone leaves the contradiction.
    """

    def test_it_says_there_are_no_tools_to_read_with(self):
        prompt = pr.triage_prompt.build_triage_prompt([], "diff")
        assert "cannot read files" in prompt

    def test_the_no_tools_line_survives_having_no_context(self):
        prompt = pr.triage_prompt.build_triage_prompt([], "")
        assert "cannot read files" in prompt

    def test_with_no_code_the_evidence_verdicts_are_not_offered(self):
        """Nothing to cite means the two posted-outward verdicts are unreachable."""
        prompt = pr.triage_prompt.build_triage_prompt([], "")
        assert "no code context was available" in prompt
        assert str(Verification.NEEDS_DISCUSSION) in prompt

    # passes-at-base: the with-context path is unchanged; this pins that it stayed so
    def test_with_code_the_evidence_requirement_stands(self):
        prompt = pr.triage_prompt.build_triage_prompt([], "some code")
        assert "no code context was available" not in prompt
        assert "REQUIRED for" in prompt


class TestTheSchemaExampleIsValidJson:
    """The prompt closes with "Return ONLY the JSON object", so the shape it
    shows has to be one.

    The `comment_items` block is assembled as a separate non-f-string and
    spliced into an f-string template, so its braces are not collapsed by the
    same pass that collapses the rest. Doubling them there once put `{{` in
    front of the model beside single-braced `threads` and `stats`.

    Parsing the block is what makes this hold for any brace mistake rather than
    for the doubled pair alone.
    """

    COMMENTS = [
        {
            "id": "c1",
            "body": "rename the flag",
            "user": "kgn",
            "source_type": "issue_comment",
        },
    ]

    @staticmethod
    def _schema(prompt: str) -> dict:
        """The schema example, parsed.

        Asserting on the marker rather than letting `index` raise: the prompt
        owns that wording and may reword it, and a bare `ValueError` from the
        slice would read as a brace regression rather than as the rename it is.
        """
        marker = "Return JSON matching this exact schema:"
        assert marker in prompt, f"prompt no longer says {marker!r} — retarget this test"
        tail = prompt[prompt.index(marker) + len(marker):]
        return json.loads(tail[tail.index("{"):tail.rindex("}") + 1])

    def test_the_schema_parses_with_decomposed_comments(self):
        prompt = pr.triage_prompt.build_triage_prompt(
            [], "diff", unseen_comments=self.COMMENTS,
        )
        assert set(self._schema(prompt)) == {"threads", "comment_items"}

    def test_the_schema_parses_without_them(self):
        """Pairs with the case above: the branch that omits the block was the
        only one under test, so its passing said nothing about the other."""
        assert set(self._schema(pr.triage_prompt.build_triage_prompt([], "diff"))) == {
            "threads",
        }

    def test_the_model_is_not_asked_for_counts_it_cannot_keep_current(self):
        """No `stats` block, in either branch.

        The tally is counted from the entries after the code has finished
        reclassifying them, so asking the model for it too would give two
        answers to one question and ship the stale one.
        """
        for prompt in (
            pr.triage_prompt.build_triage_prompt([], "diff"),
            pr.triage_prompt.build_triage_prompt(
                [], "diff", unseen_comments=self.COMMENTS,
            ),
        ):
            assert "stats" not in self._schema(prompt)

    def test_the_comment_item_fields_survive_the_splice(self):
        """Guards the repair as well as the defect: single-bracing by deleting
        the block would also make the prompt parse."""
        schema = self._schema(
            pr.triage_prompt.build_triage_prompt(
                [], "diff", unseen_comments=self.COMMENTS,
            ),
        )
        assert "source_id" in schema["comment_items"][0]
        assert "classification" in schema["comment_items"][0]
