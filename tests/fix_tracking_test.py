"""Tests for the tracking file a fix pass hands its agent.

The file is a serialization format, so the tests are round trips: render items,
edit the result the way an agent would, and check the parse says what the edit
meant. Nothing here mocks the file — a format whose reader is tested against a
hand-built string is a format with two spellings.
"""

import sys
from pathlib import Path

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

from fix import tracking as fix_tracking  # noqa: E402
from fix.types import FixItem  # noqa: E402
from pr.fix import FixOutcome  # noqa: E402


def _items():
    return [
        FixItem(id="T1", file="ai/lib/land.py", line=42, label="@reviewer",
                body="**Summary:** stage by name\n"),
        FixItem(id="T2", file="docs/guide.md", label="@other",
                body="**Summary:** wrong section\n"),
        FixItem(id="T3", label="@third", body=""),
    ]


def _tick(text: str, section_id: str, label: str, reason: str = "") -> str:
    """Tick one box in one section, the way an agent's Edit would."""
    marker = f"## <!-- fix:{section_id} -->"
    head, _, tail = text.partition(marker)
    suffix = f" — {reason}" if reason else ""
    old = f"- [ ] {label}"
    replacement = f"- [x] {label}{suffix}"
    # Only within this section: the same box label appears under every id.
    end = tail.find("\n## <!-- fix:")
    body, rest = (tail[:end], tail[end:]) if end >= 0 else (tail, "")
    if reason:
        body = body.replace(f"{old} — <why>", replacement, 1)
    else:
        body = body.replace(old, replacement, 1)
    return head + marker + body + rest


def _shown_boxes(text: str) -> list[str]:
    """The ticked spellings the instruction shows, in the order it shows them."""
    return [
        line.split("`")[1]
        for line in text.splitlines()
        if line.startswith("- `- [x] ")
    ]


class TestRender:
    def test_every_section_carries_its_id_and_the_three_boxes(self):
        text = fix_tracking.render("Comment Fix Tracking — PR #7", _items())
        assert text.startswith("# Comment Fix Tracking — PR #7\n")
        assert "## <!-- fix:T1 --> ai/lib/land.py:42 — @reviewer" in text
        assert "## <!-- fix:T2 --> docs/guide.md — @other" in text
        # No file at all still gets a heading with a location in it.
        assert "## <!-- fix:T3 --> — — @third" in text
        assert text.count("- [ ] fixed — <why>\n") == 3
        assert text.count("- [ ] declined — <why>\n") == 3
        assert text.count("- [ ] needs a person — <why>\n") == 3

    def test_the_body_the_domain_rendered_survives_verbatim(self):
        text = fix_tracking.render("t", [FixItem(id="A", body="```diff\n- x\n```")])
        assert "```diff\n- x\n```" in text

    def test_write_creates_the_directory_it_needs(self, tmp_path):
        path = tmp_path / "ignore" / "pr-comments" / "fix-tracking.md"
        fix_tracking.write(path, "t", _items())
        assert path.read_text() == fix_tracking.render("t", _items())


class TestParse:
    def test_an_untouched_file_is_work_still_owed(self, tmp_path):
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", _items())
        outcomes = fix_tracking.parse(path)
        assert [o.id for o in outcomes] == ["T1", "T2", "T3"]
        assert {o.outcome for o in outcomes} == {FixOutcome.DEFERRED}
        assert all(o.reason == "" for o in outcomes)

    def test_the_anchor_comes_back_off_the_heading(self, tmp_path):
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", _items())
        by_id = {o.id: o for o in fix_tracking.parse(path)}
        assert (by_id["T1"].file, by_id["T1"].line) == ("ai/lib/land.py", 42)
        assert (by_id["T2"].file, by_id["T2"].line) == ("docs/guide.md", 0)
        assert (by_id["T3"].file, by_id["T3"].line) == ("", 0)

    def test_each_box_maps_to_its_outcome_and_keeps_its_reason(self, tmp_path):
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", _items())
        text = _tick(path.read_text(), "T1", "fixed")
        text = _tick(text, "T2", "declined", "the helper it names does not exist")
        text = _tick(text, "T3", "needs a person", "wants a design call")
        path.write_text(text)

        by_id = {o.id: o for o in fix_tracking.parse(path)}
        assert by_id["T1"].outcome == FixOutcome.FIXED
        assert by_id["T1"].reason == ""
        assert by_id["T2"].outcome == FixOutcome.DECLINED
        assert by_id["T2"].reason == "the helper it names does not exist"
        assert by_id["T3"].outcome == FixOutcome.NEEDS_HUMAN
        assert by_id["T3"].reason == "wants a design call"

    def test_a_box_ticked_with_the_placeholder_left_in_reports_no_reason(self, tmp_path):
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", [FixItem(id="A")])
        path.write_text(path.read_text().replace(
            "- [ ] declined — <why>", "- [x] declined — <why>",
        ))
        outcome = fix_tracking.parse(path)[0]
        assert outcome.outcome == FixOutcome.DECLINED
        assert outcome.reason == ""

    def test_a_box_annotated_the_agent_s_own_way_still_counts(self, tmp_path):
        """The em dash is what the render writes, not what the agent has to write.

        A tick the parse cannot see reads as work still owed, so the same item
        goes back to a second pass that redoes what the first already did.
        """
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", [FixItem(id="A")])
        path.write_text(path.read_text().replace(
            "- [ ] declined — <why>", "- [x] declined: the premise does not hold",
        ))
        outcome = fix_tracking.parse(path)[0]
        assert outcome.outcome == FixOutcome.DECLINED
        assert outcome.reason == "the premise does not hold"
        assert fix_tracking.checked(path) == 1

    def test_the_test_evidence_written_after_fixed_is_kept(self, tmp_path):
        """The fix box asks what test holds the change; the parse reads it back.

        The evidence is the whole point of the box asking. A parse that dropped
        it would leave an operator with a ticked box and no way to tell a fix
        with a regression test from one without.
        """
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", [FixItem(id="A")])
        path.write_text(path.read_text().replace(
            "- [ ] fixed — <why>",
            "- [x] fixed — test_the_guard_rejects_an_empty_name",
        ))
        outcome = fix_tracking.parse(path)[0]
        assert outcome.outcome == FixOutcome.FIXED
        assert outcome.reason == "test_the_guard_rejects_an_empty_name"

    def test_a_fix_ticked_without_evidence_still_reads_as_fixed(self, tmp_path):
        """The ask is a prompt contract, not a parse-time gate.

        An agent that ticks the box and leaves `<why>` standing has applied the
        change — refusing to read that as FIXED would discard real work over a
        missing sentence, and send a later pass back over a finding already
        answered.
        """
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", [FixItem(id="A")])
        path.write_text(path.read_text().replace(
            "- [ ] fixed — <why>", "- [x] fixed — <why>",
        ))
        outcome = fix_tracking.parse(path)[0]
        assert outcome.outcome == FixOutcome.FIXED
        assert outcome.reason == ""

    def test_a_fix_with_no_evidence_is_still_logged(self, tmp_path, capsys):
        """FIXED with no reason is accepted, but not silently.

        The parse does not gate on the reason (see the test above), so a
        warning is the only trace that an agent ticked `fixed` and left `<why>`
        standing — without it, the exact failure this contract exists to catch
        leaves no record an operator would ever see.
        """
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", [FixItem(id="A")])
        path.write_text(path.read_text().replace(
            "- [ ] fixed — <why>", "- [x] fixed — <why>",
        ))
        fix_tracking.parse(path)
        warning = capsys.readouterr().err
        assert "A" in warning
        assert "no test evidence" in warning

    def test_a_fix_that_landed_outranks_a_position_argued_beside_it(self, tmp_path):
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", [FixItem(id="A")])
        text = _tick(path.read_text(), "A", "needs a person", "unsure")
        path.write_text(_tick(text, "A", "fixed"))
        assert fix_tracking.parse(path)[0].outcome == FixOutcome.FIXED

    def test_one_section_s_boxes_do_not_answer_for_the_next(self, tmp_path):
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", _items())
        path.write_text(_tick(path.read_text(), "T2", "fixed"))
        by_id = {o.id: o for o in fix_tracking.parse(path)}
        assert by_id["T2"].outcome == FixOutcome.FIXED
        assert by_id["T1"].outcome == FixOutcome.DEFERRED
        assert by_id["T3"].outcome == FixOutcome.DEFERRED

    def test_a_file_the_pass_never_wrote_is_no_outcomes(self, tmp_path):
        assert fix_tracking.parse(tmp_path / "absent.md") == []

    def test_an_uppercase_tick_counts(self, tmp_path):
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", [FixItem(id="A")])
        path.write_text(path.read_text().replace("- [ ] fixed", "- [X] fixed"))
        assert fix_tracking.parse(path)[0].outcome == FixOutcome.FIXED


class TestInstructions:
    def test_it_spells_every_box_the_render_writes(self):
        boxes = _shown_boxes(fix_tracking.instructions("finding"))
        assert boxes == [
            "- [x] fixed — <why>",
            "- [x] declined — <why>",
            "- [x] needs a person — <why>",
        ]

    def test_the_domain_s_own_word_for_an_item_is_what_the_agent_reads(self):
        text = fix_tracking.instructions("thread")
        assert "Every thread above carries three boxes" in text
        assert "finding" not in text
        assert "{noun}" not in text

    def test_an_answer_spelled_the_way_the_instruction_shows_parses_back(self, tmp_path):
        """The instruction is only true while the parse agrees with it.

        A box the prompt names one way and the reader looks for another way ticks
        nothing the parse can see, so every item comes back reading as work still
        owed and no gate fails. The spelling is round tripped rather than asserted
        against a second copy of itself for the same reason the rest of this file
        is: a copy agrees with whatever it was written beside.
        """
        outcomes = []
        for i, box in enumerate(_shown_boxes(fix_tracking.instructions("finding"))):
            path = tmp_path / f"t{i}.md"
            fix_tracking.write(path, "t", [FixItem(id="A")])
            answered = box.replace("<why>", "the premise does not hold")
            path.write_text(path.read_text().replace(
                box.replace("- [x]", "- [ ]", 1), answered, 1,
            ))
            outcomes.append(fix_tracking.parse(path)[0].outcome)

        assert outcomes == [
            FixOutcome.FIXED, FixOutcome.DECLINED, FixOutcome.NEEDS_HUMAN,
        ]


class TestChecked:
    def test_it_counts_boxes_rather_than_items(self, tmp_path):
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", _items())
        assert fix_tracking.checked(path) == 0
        path.write_text(_tick(path.read_text(), "T1", "fixed"))
        assert fix_tracking.checked(path) == 1

    def test_a_position_counts_as_output_the_same_as_a_fix(self, tmp_path):
        path = tmp_path / "t.md"
        fix_tracking.write(path, "t", [FixItem(id="A")])
        path.write_text(_tick(path.read_text(), "A", "declined", "not a defect"))
        assert fix_tracking.checked(path) == 1

    def test_a_file_the_pass_never_wrote_counts_nothing(self, tmp_path):
        assert fix_tracking.checked(tmp_path / "absent.md") == 0


# ── the verify gate's vocabulary ────────────────────────────────────────────


class TestVerifyVerdicts:
    """The gate answers whether a fix holds up, not whether one was applied.

    A separate vocabulary rather than a reuse of the fix boxes: an agent that
    ticked `fixed` here would be answering the wrong question, and the parser
    must not accept it as an answer to this one.
    """

    def _file(self, tmp_path, body):
        path = tmp_path / "verify.md"
        path.write_text(body)
        return path

    def test_a_verified_box_reads_as_true_with_its_evidence(self, tmp_path):
        path = self._file(tmp_path, (
            "# Verify\n\n## <!-- fix:t1 --> a.py:1 — x\n\n"
            "- [x] verified — ran the reviewer's repro, exits 0 now\n"
        ))
        assert fix_tracking.parse_verdicts(path) == {
            "t1": (True, "ran the reviewer's repro, exits 0 now"),
        }

    def test_a_broken_box_reads_as_false(self, tmp_path):
        path = self._file(tmp_path, (
            "# Verify\n\n## <!-- fix:t1 --> a.py:1 — x\n\n"
            "- [x] broken — repro still exits 3\n"
        ))
        assert fix_tracking.parse_verdicts(path) == {"t1": (False, "repro still exits 3")}

    def test_not_verified_reads_as_none_and_keeps_its_reason(self, tmp_path):
        """The middle answer is the one most fixes will get, and why matters."""
        path = self._file(tmp_path, (
            "# Verify\n\n## <!-- fix:t1 --> a.py:1 — x\n\n"
            "- [x] not verified — every test on this path mocks the parser\n"
        ))
        assert fix_tracking.parse_verdicts(path) == {
            "t1": (None, "every test on this path mocks the parser"),
        }

    def test_an_unanswered_section_is_absent_rather_than_none(self, tmp_path):
        """Absent and None are different: one has a reason, the other has nothing.

        Collapsing them would print an empty explanation on a row the gate
        simply never reached.
        """
        path = self._file(tmp_path, (
            "# Verify\n\n## <!-- fix:t1 --> a.py:1 — x\n\n"
            "- [ ] verified — <why>\n- [ ] not verified — <why>\n"
        ))
        assert fix_tracking.parse_verdicts(path) == {}

    def test_a_fix_pass_box_is_not_an_answer_here(self, tmp_path):
        """`fixed` answers a different question and must not read as a verdict."""
        path = self._file(tmp_path, (
            "# Verify\n\n## <!-- fix:t1 --> a.py:1 — x\n\n- [x] fixed\n"
        ))
        assert fix_tracking.parse_verdicts(path) == {}

    def test_several_sections_each_get_their_own_verdict(self, tmp_path):
        path = self._file(tmp_path, (
            "# Verify\n\n"
            "## <!-- fix:t1 --> a.py:1 — x\n\n- [x] verified — ran it\n\n"
            "## <!-- fix:t2 --> b.py:2 — y\n\n- [x] broken — still fails\n"
        ))
        assert fix_tracking.parse_verdicts(path) == {
            "t1": (True, "ran it"), "t2": (False, "still fails"),
        }

    def test_a_file_that_was_never_written_is_no_verdicts(self, tmp_path):
        assert fix_tracking.parse_verdicts(tmp_path / "absent.md") == {}

    def test_the_render_writes_the_verify_boxes(self, tmp_path):
        text = fix_tracking.render(
            "Verify", [FixItem(id="t1", file="a.py", line=1, label="x")],
            fix_tracking.VERIFY_BOXES,
        )
        assert "- [ ] verified" in text
        assert "- [ ] not verified" in text
        assert "- [ ] broken" in text
        assert "- [ ] fixed" not in text


class TestEveryVerifyVerdictAsksForEvidence:
    """A verdict with no reason is a verdict nobody can act on.

    Both vocabularies ask for evidence on every box, for the same underlying
    reason: "what did you run" is the entire evidentiary value of a verify
    verdict, and "what test holds this" is the same question asked of a fix
    before anyone has run anything.
    """

    def test_all_three_boxes_carry_the_placeholder(self):
        text = fix_tracking.render(
            "Verify", [FixItem(id="t1", file="a.py", line=1, label="x")],
            fix_tracking.VERIFY_BOXES,
        )
        for label in ("verified", "not verified", "broken"):
            assert f"- [ ] {label} — <why>" in text, f"{label} asks for no evidence"

    def test_the_fix_boxes_are_unchanged_by_the_verify_vocabulary(self):
        """The two sets ask the same shape now, but must stay separate sets."""
        text = fix_tracking.render("Fix", [FixItem(id="t1", file="a.py", line=1)])
        assert "- [ ] fixed — <why>" in text
        assert "- [ ] declined — <why>" in text
        for label in ("verified", "not verified", "broken"):
            assert f"- [ ] {label}" not in text


class TestEveryFixTemplateAsksForTheTest:
    """The `fixed` box means "behaviour holds", not "a file was edited".

    Five consecutive fix passes are the reason this is pinned. Four edited code
    and shipped no assertion; the fifth added a case for an accented path while
    reworking the common-path setup it left uncovered. Every one of them ticked
    `fixed` truthfully under the old contract, because the contract asked only
    that an edit be made.

    The shared half of the ask lives in `instructions()` and reaches every
    domain through `${answer_format}`. The domain half lives in each template,
    because what a test is worth differs per domain: a findings fix owes a
    regression test, while a CI fix's oracle is the check that was already
    failing. Both halves are pinned here so a new fix phase cannot arrive
    without one.
    """

    def _fix_templates(self):
        """Every phase the fix runner serves, with its template text.

        Enumerated from the registry rather than listed by hand: a phase added
        later is a phase this test must cover, and a hardcoded list would let
        it through while still reporting green.
        """
        from agent import templates as agent_templates
        from agent.registry import PHASES
        from core.phases import PhaseShape

        root = agent_templates.template_dir()
        # Newlines collapsed: these are wrapped prose files, so a phrase the
        # contract asks for may be split across two lines. Matching the raw
        # text would make the assertion depend on where the wrap happened to
        # fall, and a rewrap would fail a template that still says the thing.
        return {
            phase: " ".join((root / spec.template_for()).read_text().split())
            for phase, spec in PHASES.items()
            if spec.shape is PhaseShape.FIX
            # The verify gate answers a different question in a different
            # vocabulary — it is forbidden to edit source, so it can never be
            # the pass that adds a test.
            and spec.template_for() != "verify-fixes.md"
        }

    def test_the_shared_ask_names_a_test_and_the_way_out_of_one(self):
        text = fix_tracking.instructions("finding")
        assert "fails without it" in text
        assert "regression test" in text
        # The exemption has to be offered in the same breath, or the honest
        # answer for a prose fix is to tick the box and say nothing.
        assert "does not apply" in text

    def test_every_fix_template_says_what_its_domain_owes(self):
        for phase, text in self._fix_templates().items():
            assert "`fixed` box asks for" in text, (
                f"{phase} never tells its agent what the fixed box asks for"
            )

    def test_the_two_behaviour_domains_ask_for_a_regression_test(self):
        """Findings and comments both change behaviour, so both owe a test."""
        templates = self._fix_templates()
        for phase in ("fix", "comments_fix"):
            text = templates[phase]
            assert "fails without your change" in text, (
                f"{phase} does not say the test must fail without the fix"
            )

    def test_the_check_driven_domains_ask_for_the_check_instead(self):
        """CI and pre-push already have an oracle; a new test is not the ask.

        Demanding one here would be the cargo-cult version of this rule: the
        failing check is the thing that proves the fix, and a test invented to
        satisfy a checklist is the vacuous kind this whole change exists to
        stop.
        """
        templates = self._fix_templates()
        for phase in ("ci_fix", "prepush_fix"):
            assert "name the check you re-ran" in templates[phase], (
                f"{phase} does not ask which check was re-run"
            )
