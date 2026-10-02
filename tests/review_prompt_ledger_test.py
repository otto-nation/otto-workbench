"""review.prompt_prior: the unaccounted-prior section and the ledger instruction."""

import re
import string
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.types import (
    DISPOSITION_TAIL_PROSE,
    DISPOSITION_TAIL_PUNCTUATION,
    FindingRef,
    PriorDisposition,
    PriorFinding,
)
from core.phases import Phase

from review.grammar import parse_ledger_line
from review.prompt import _build_common_sections
from review.prompt_prior import _LEDGER_INSTRUCTION, _build_unaccounted_section
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
        assert _LEDGER_INSTRUCTION in _build_unaccounted_section([self.M1])

    def test_the_findings_come_before_the_instruction_that_says_above(self):
        section = _build_unaccounted_section([self.M1])
        assert section.index("</unaccounted_findings>") < section.index(_LEDGER_INSTRUCTION)

    def test_the_synthesis_prompt_carries_them(self):
        job = _make_job(_make_preflight())
        common = _build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES)
        extra = dict(
            group_count=1, merged_content="m", holistic_content="h",
            unaccounted_prior=[self.M1],
        )
        built = review.registry.for_phase(Phase.SYNTHESIS).build(job, common, extra, "/tmp/r.md")
        assert "drops the error" in built.builder.vars["prior_section"]

    def test_a_synthesis_prompt_with_nothing_left_over_says_nothing(self):
        job = _make_job(_make_preflight())
        common = _build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES)
        extra = dict(group_count=1, merged_content="m", holistic_content="h")
        built = review.registry.for_phase(Phase.SYNTHESIS).build(job, common, extra, "/tmp/r.md")
        assert built.builder.vars["prior_section"] == ""


# ── The ledger instruction and the ledger parser ────────────────────────────


class TestLedgerInstructionParses:
    """The form asked for and the form accepted cannot drift apart.

    The instruction is the only description of the ledger an agent ever reads,
    and reconciliation is the only thing that reads what it writes back. An
    example the parser rejects is therefore invisible until a whole re-review's
    bookkeeping is lost, which is what happened when the verdicts the reviews
    wrote ended in a full stop and the examples all used an em dash.
    """

    def _examples(self):
        """The ledger lines the instruction shows, as an agent would copy them.

        Each is a backticked span whose own backticks are escaped, so the one
        that closes it is the first that no backslash precedes.
        """
        spans = (re.match(r"^- `(.+?)(?<!\\)`", line)
                 for line in _LEDGER_INSTRUCTION.split("\n"))
        return [m.group(1).replace("\\`", "`") for m in spans if m]

    def test_the_instruction_shows_every_verdict(self):
        shown = [e for e in self._examples() for d in PriorDisposition if d.value in e]
        assert len(shown) == len(list(PriorDisposition))

    def test_every_example_parses_to_the_verdict_it_names(self):
        examples = self._examples()
        assert examples, "the instruction shows no ledger lines"
        for example in examples:
            entry = parse_ledger_line(example)
            assert entry, f"the instruction's example does not parse: {example}"
            assert entry.disposition and entry.disposition.value in example


class TestLedgerInstructionNamesEveryBreak:
    """Every break the parser takes is one the instruction told an agent about.

    The prose has gone stale twice — once omitting the opening bracket and the
    en dash, once the emphasis characters the same commit added. The prose is
    built from `DISPOSITION_TAIL_PROSE` rather than retyped, so what this pins
    is the map itself: a character added to the parser's class with no words for
    it, or with words the sentence never reaches, fails here instead of leaving
    the prompt describing a shape the parser has outgrown.
    """

    def test_every_accepted_character_has_words_for_it(self):
        for char in DISPOSITION_TAIL_PUNCTUATION:
            assert DISPOSITION_TAIL_PROSE.get(char), f"no prose names {char!r}"

    def test_the_instruction_says_every_one_of_those_words(self):
        for char, prose in DISPOSITION_TAIL_PROSE.items():
            assert prose in _LEDGER_INSTRUCTION, (
                f"the instruction never says {prose!r}, the wording for {char!r}"
            )

    def test_a_verdict_broken_by_each_character_parses(self):
        """The words are only worth pinning if what they describe is accepted."""
        for char in DISPOSITION_TAIL_PUNCTUATION:
            line = f"- **[M1]** `handler.go` — {PriorDisposition.FIXED} {char}detail{char}"
            entry = parse_ledger_line(line)
            assert entry and entry.disposition is PriorDisposition.FIXED, (
                f"{char!r} is named in the instruction but breaks no verdict"
            )

    def test_the_parser_accepts_nothing_the_instruction_does_not_name(self):
        """Asked of the parser, not of the map it is built from.

        Widening the accepted set by editing the regex rather than the map would
        satisfy every assertion above — they all iterate the map, so a character
        missing from it is a character they never ask about. This probes the
        parser with every punctuation mark instead, so whichever end is widened,
        an unnamed break fails here.
        """
        candidates = set(string.punctuation) | set("—–―−")
        accepted = {
            c for c in candidates
            if PriorDisposition.parse(f"{PriorDisposition.FIXED} {c}detail")
            is PriorDisposition.FIXED
        }
        assert accepted == set(DISPOSITION_TAIL_PROSE), (
            "the parser breaks a verdict on characters the instruction never "
            f"names: {sorted(accepted - set(DISPOSITION_TAIL_PROSE))}"
        )
