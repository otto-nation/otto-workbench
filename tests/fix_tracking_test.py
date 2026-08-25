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

import fix_tracking  # noqa: E402
from fix_types import FixItem  # noqa: E402
from pr_fix import FixOutcome  # noqa: E402


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
        body = body.replace(f"{old} — {fix_tracking._WHY}", replacement, 1)
    else:
        body = body.replace(old, replacement, 1)
    return head + marker + body + rest


class TestRender:
    def test_every_section_carries_its_id_and_the_three_boxes(self):
        text = fix_tracking.render("Comment Fix Tracking — PR #7", _items())
        assert text.startswith("# Comment Fix Tracking — PR #7\n")
        assert "## <!-- fix:T1 --> ai/lib/land.py:42 — @reviewer" in text
        assert "## <!-- fix:T2 --> docs/guide.md — @other" in text
        # No file at all still gets a heading with a location in it.
        assert "## <!-- fix:T3 --> — — @third" in text
        assert text.count("- [ ] fixed\n") == 3
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
