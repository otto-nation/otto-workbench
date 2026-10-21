"""review.prompt_prior: the unaccounted-prior section and the ledger instruction."""

import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.types import FindingRef, PriorDisposition, PriorFinding
from core.phases import Phase

from review.prompt import build_common_sections
from core.strict import load_strict
from review.prompt_prior import (
    _LEDGER_FIELDS_INSTRUCTION, _build_unaccounted_section,
)
from review.record import LedgerLine
import review.registry

from review_prompt_support import MAX_PROMPT_BYTES, _make_preflight, _make_job


# ── The prior findings handed to synthesis to settle ────────────────────────


def _passed_over(finding_id, path, text):
    return PriorFinding(FindingRef(finding_id, path), "sid", text)


class TestUnaccountedPriorSection:
    """Synthesis's `prior_section` is the remainder, not the prior review.

    The group agents were each shown their slice of the prior review and their
    conclusions are already in the merged content. What reaches synthesis is
    what none of them accounted for — the last point in the run where an agent
    can still decide it.
    """

    M1 = _passed_over(
        "M1", "handler.go",
        "- **[M1]** **`handler.go:42`** — `rows, _ := db.Query(sql)` drops the error",
    )

    def test_nothing_passed_over_renders_no_section(self):
        assert _build_unaccounted_section([]) == ""

    def test_a_finding_arrives_with_the_text_that_reported_it(self):
        section = _build_unaccounted_section([self.M1])
        assert "**[M1]**" in section
        assert "`rows, _ := db.Query(sql)` drops the error" in section

    def test_the_section_asks_for_the_ledger_the_parser_reads(self):
        """One owner for the ledger's shape — this section states none of its own."""
        assert _LEDGER_FIELDS_INSTRUCTION in _build_unaccounted_section([self.M1])

    def test_the_findings_come_before_the_instruction_that_says_above(self):
        section = _build_unaccounted_section([self.M1])
        assert section.index("</unaccounted_findings>") < section.index(_LEDGER_FIELDS_INSTRUCTION)

    def test_the_synthesis_prompt_carries_them(self):
        job = _make_job(_make_preflight())
        common = build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES)
        extra = dict(
            group_count=1, merged_content="m", holistic_content="h",
            unaccounted_prior=[self.M1],
        )
        built = review.registry.for_phase(Phase.SYNTHESIS).build(job, common, extra, "/tmp/r.md")
        assert "drops the error" in built.builder.vars["prior_section"]

    def test_a_synthesis_prompt_with_nothing_left_over_says_nothing(self):
        job = _make_job(_make_preflight())
        common = build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES)
        extra = dict(group_count=1, merged_content="m", holistic_content="h")
        built = review.registry.for_phase(Phase.SYNTHESIS).build(job, common, extra, "/tmp/r.md")
        assert built.builder.vars["prior_section"] == ""


def test_the_synthesis_remainder_asks_for_the_json_ledger():
    section = _build_unaccounted_section([TestUnaccountedPriorSection.M1])
    assert _LEDGER_FIELDS_INSTRUCTION in section
    assert "`prior_findings`" in section


class TestLedgerFieldsInstruction:
    """The JSON ledger the prompt asks for is the ledger the reader accepts."""

    def test_the_example_entry_is_a_ledger_line(self):
        example = re.search(r"Example entry: (\{.*\})", _LEDGER_FIELDS_INSTRUCTION).group(1)
        assert load_strict(LedgerLine, json.loads(example)).disposition is PriorDisposition.FIXED

    def test_every_disposition_is_offered_by_its_exact_value(self):
        for d in PriorDisposition:
            assert f'"{d.value}"' in _LEDGER_FIELDS_INSTRUCTION
