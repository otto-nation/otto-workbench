"""Cross-file contract tests for prose the review templates must carry — the
Summary's ban on a prior-findings tally and the execution-claim guard."""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
TEMPLATE_DIR = LIB_DIR / "review-templates"
BIN_DIR = REPO_ROOT / "ai" / "bin"
AGENTS_DIR = REPO_ROOT / "ai" / "claude" / "agents"

# Insert lib dir so we can import the review modules directly
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.templates  # noqa: E402
from agent.registry import PHASES  # noqa: E402
from core.phases import PhaseShape  # noqa: E402

# Every source that tells an agent what to write under `## Summary`. The
# constraint below is stated in each rather than shared from one place: two of
# them phrase it mid-clause in a numbered task list and two inside a fenced
# output skeleton, and `review.prompt`'s `_REREVIEW_CTX` sets the house
# precedent of spelling per-phase instructional prose out in full. The copies
# are kept honest here instead.
_SUMMARY_CONTRACT_RE = re.compile(r"(?m)(?:^## Summary\b|Write ## Summary)")

# A tally of prior findings in the Summary is prose no one computes: the
# ledger that does record them is stripped before publish, so a wrong count
# reaches the PR as the only surviving statement about the prior round.
_NO_PRIOR_TALLY = (
    "On a re-review, do not count the prior findings here "
    '("13 of 19 fixed") — the `## Prior findings` ledger is the record of '
    "what became of them, and a tally written twice is a tally that can "
    "disagree with itself"
)


def _summary_contract_sources() -> list[Path]:
    """Every template or agent file that states a `## Summary` contract.

    Scraped rather than listed, so a fifth one added later is held to the same
    constraint without this file being edited. The pattern is anchored on the
    heading so a backticked cross-reference to `## Summary` mid-sentence does
    not read as a contract.
    """
    sources = [AGENTS_DIR / "reviewer.md", *sorted(TEMPLATE_DIR.glob("*.md"))]
    return [
        path for path in sources
        if path.exists() and _SUMMARY_CONTRACT_RE.search(path.read_text())
    ]


# passes-at-base: asserts the selector's reach, which the constraint did not change
def test_summary_contract_sources_are_found():
    """The selector finds the known contracts, so the check below is not vacuous.

    Holds at the merge base by design: the four files stated a Summary contract
    before they carried the constraint. What it guards is the parametrised test
    below, which would pass over an empty set without saying so — a selector
    that stops matching is the way that check goes quietly green.
    """
    names = {p.name for p in _summary_contract_sources()}
    assert names == {
        "reviewer.md",
        "self-review.md",
        "self-review-synthesis.md",
        "synthesis.md",
    }, f"unexpected set of Summary contracts: {sorted(names)}"


@pytest.mark.parametrize(
    "path",
    _summary_contract_sources(),
    ids=[p.name for p in _summary_contract_sources()],
)
def test_summary_contract_forbids_a_prior_findings_tally(path):
    """Every `## Summary` contract carries the no-tally constraint, verbatim."""
    assert _NO_PRIOR_TALLY in path.read_text(), (
        f"{path.name} states a `## Summary` contract without the "
        f"no-prior-tally constraint. Add this sentence verbatim:\n\n{_NO_PRIOR_TALLY}"
    )


# A write-first template tells the agent to write its file before reading any
# source, and then invites findings drafted from nothing but what is already
# in the prompt. Such a finding reaches the reader unverified unless something
# tells the agent not to claim it ran a check it never ran.
#
# The guard is substituted, not written into each template: `${...}` in the
# file, `build_execution_claim_guard` behind it. So the contract these hold is
# that every write-first template *renders* it — asserting the prose appears
# in the file would now fail on all five and pass on a template that dropped
# the placeholder.
_EXECUTION_CLAIM_PLACEHOLDER = "${execution_claim_guard}"
_NO_UNRUN_EXECUTION_CLAIM = (
    "Never write that you ran something unless you ran it in this session."
)

# Two things make a template need the guard, and they are selected two ways
# because they are two different properties.
#
# The first is a write-first instruction: the agent is told to write its file
# before investigating, so a claim drafted there describes a command that has
# not run. That is a property of the prose, so it is scraped from the prose.
#
# The second is a fix pass's tracking file, whose `fixed` box asks by name for
# the test that fails without the change — the form itself invites naming a
# plausible test over running one. That is a property of the *phase*, not of
# any wording, so it is read off the registry: every `PhaseShape.FIX` phase's
# template, with no list here to drift from the tree.
#
# Templates with neither (disprove.md, which writes its verdicts last, after
# investigating; holistic.md and scout.md, which author no findings) are out
# of scope.
#
# Deliberately two alternatives, not three: an earlier version also matched
# the bare substring "file FIRST", which is redundant with "do not investigate
# before that first write" for every template today (both match the same
# three files) but would pull in a future template on wording coincidence
# alone — e.g. "Write your file FIRST, then verify" names no ban on
# investigating first and has no business tripping this guard.
_WRITE_FIRST_RE = re.compile(
    r"FIRST action must be writing"
    r"|do not investigate before that first write"
)


def _fix_shape_templates() -> set[str]:
    """Every template a fix pass renders, read off the phase registry.

    Structural rather than a name pattern: `fix-*.md` would miss
    verify-fixes.md and would match a future template called fix-something
    that no phase renders. The registry is what actually decides which
    template an agent is handed.
    """
    return {
        PHASES[phase].template_for()
        for phase, spec in PHASES.items()
        if spec.shape is PhaseShape.FIX
    }


def _execution_claim_templates() -> list[Path]:
    """Every template whose agent can author an unrun execution claim.

    Scraped and derived rather than listed, following
    `_summary_contract_sources` above: a write-first template or a fix phase
    added later is held to the same constraint without this file being
    edited. A hardcoded list is how the guard reached four of five templates
    twice running — the list and the tree drift, and the test passes on the
    files someone remembered.
    """
    fix_templates = _fix_shape_templates()
    return [
        path for path in sorted(TEMPLATE_DIR.glob("*.md"))
        if _WRITE_FIRST_RE.search(path.read_text()) or path.name in fix_templates
    ]


# A non-vacuity pin on the selector, which reads the registry and the prose —
# neither of which this change alters. The behaviour test it guards,
# test_template_forbids_unverified_execution_claims, does fail at base.
# passes-at-base: it pins the selector, not the guard the selector feeds
def test_execution_claim_templates_are_found():
    """The selector finds both populations, so the check below is not vacuous.

    A selector that matched nothing would make the parametrized assertion
    pass by having no cases at all. Pinned as an exact set so a template that
    silently leaves the scope is a failure rather than one fewer case.
    """
    found = {path.name for path in _execution_claim_templates()}
    assert found == {
        # write-first, scraped from the prose
        "group.md", "self-review.md", "self-review-synthesis.md",
        "single-agent.md", "synthesis.md",
        # fix-shape, derived from the phase registry
        "fix-findings.md", "verify-fixes.md", "fix-comments.md",
        "fix-ci.md", "fix-prepush.md",
    }, f"unexpected set of execution-claim templates: {sorted(found)}"


# Pure non-vacuity on the two selectors: the write-first one predates this
# change and the other reads the phase registry.
# passes-at-base: it asserts the selectors match something, not what they feed
def test_both_populations_are_non_empty():
    """Either selector silently matching nothing would halve the guard's scope.

    The union above would still look healthy on the remaining half, and the
    exact-set assertion would be the only thing standing between that and a
    population nobody checks.
    """
    write_first = {
        path.name for path in sorted(TEMPLATE_DIR.glob("*.md"))
        if _WRITE_FIRST_RE.search(path.read_text())
    }
    assert write_first, "the write-first scrape matched no template"
    assert _fix_shape_templates(), "no phase declares PhaseShape.FIX"


def test_write_first_re_does_not_match_on_file_first_wording_alone():
    """The selector is the write-before-investigate property, not a phrase.

    A future template that writes "Write your file FIRST, then verify" names
    no ban on investigating before the write and has no business being pulled
    into this guard's scope by that wording coincidence. Regression for a
    prior version of `_WRITE_FIRST_RE` that also matched the bare substring
    "file FIRST", which every current template's real alternative already
    covers.
    """
    assert not _WRITE_FIRST_RE.search("Write your file FIRST, then verify.")


@pytest.mark.parametrize(
    "path", _execution_claim_templates(), ids=lambda p: p.name,
)
def test_template_forbids_unverified_execution_claims(path):
    """Every write-first, finding-authoring template renders the guard.

    Regression for the guard landing in some of these templates but not all:
    one fix added it to synthesis.md alone and left self-review-synthesis.md,
    a template with the same write-first shape, without it. It reached four of
    five twice while it was being copied by hand, which is why the text now
    has one owner and the templates carry a placeholder.
    """
    assert _EXECUTION_CLAIM_PLACEHOLDER in path.read_text(), (
        f"{path.name}'s turn budget authors findings after a write-first "
        f"instruction without {_EXECUTION_CLAIM_PLACEHOLDER}. Add the "
        "placeholder and have the phase's builder call "
        "`b.execution_claim_guard()`."
    )


def test_execution_claim_guard_states_the_ban():
    """The substituted text is the ban itself, not an empty placeholder.

    Without this the check above passes on a builder that renders nothing:
    every template would carry `${execution_claim_guard}` and no template
    would carry a guard.
    """
    assert _NO_UNRUN_EXECUTION_CLAIM in agent.templates.build_execution_claim_guard()
    assert _NO_UNRUN_EXECUTION_CLAIM in agent.templates.build_execution_claim_guard(8)


def test_the_fix_occasion_states_the_ban_and_names_the_fixed_box():
    """A fix agent gets the same rule with its own mechanism named.

    The ban is the invariant and must survive the occasion split; the `fixed`
    box is why a fix agent in particular is about to break it. An agent told
    about a write-first sequence it has no part in is reading advice for
    somebody else and skips the paragraph.
    """
    guard = agent.templates.build_execution_claim_guard(
        occasion=agent.templates.ClaimOccasion.FIX_EVIDENCE)

    assert _NO_UNRUN_EXECUTION_CLAIM in guard
    assert "`fixed` box" in guard
    assert "first write" not in guard, "write-first rationale leaked into the fix guard"


def test_the_write_first_occasion_is_unchanged_by_the_split():
    """The default occasion still renders exactly what the five templates had."""
    assert agent.templates.build_execution_claim_guard() == (
        agent.templates.build_execution_claim_guard(
            occasion=agent.templates.ClaimOccasion.WRITE_FIRST))
    assert "first write" in agent.templates.build_execution_claim_guard()


def test_every_fix_template_renders_a_guard_with_no_placeholder_left():
    """`render` uses safe_substitute, so an unfilled placeholder reaches the agent.

    The contract check above asserts the placeholder is *in the file*; this
    asserts something fills it. Without it a template could carry
    `${execution_claim_guard}` that no render site supplies, and the agent
    would be shown the literal text.
    """
    guard = agent.templates.build_execution_claim_guard(
        occasion=agent.templates.ClaimOccasion.FIX_EVIDENCE)
    for name in sorted(_fix_shape_templates()):
        rendered = agent.templates.render(name, execution_claim_guard=guard)
        assert _EXECUTION_CLAIM_PLACEHOLDER not in rendered, name
        assert _NO_UNRUN_EXECUTION_CLAIM in rendered, name


def test_execution_claim_guard_names_the_cross_cutting_step():
    """A synthesis template's guard points at the step that adds findings.

    The two synthesis templates number that step differently, so the builder
    takes it as a parameter; a wrong number sends the agent to the wrong step.
    """
    assert "step 8" in agent.templates.build_execution_claim_guard(8)
    assert "step 9" in agent.templates.build_execution_claim_guard(9)
    assert "step" not in agent.templates.build_execution_claim_guard()


@pytest.mark.parametrize(
    ("name", "step"),
    [("synthesis.md", 8), ("self-review-synthesis.md", 9)],
)
def test_synthesis_cross_cutting_step_matches_its_template(name, step):
    """The step number the guard names is the one the template lists.

    `_SYNTHESIS_CROSS_CUTTING_STEP` lives in the prompt builder and the task
    list lives in the template, so nothing but this holds them together —
    renumbering the list would otherwise leave the guard citing a step that
    says something else.
    """
    from review.prompt import _SYNTHESIS_CROSS_CUTTING_STEP

    assert step in _SYNTHESIS_CROSS_CUTTING_STEP.values()
    body = (TEMPLATE_DIR / name).read_text()
    assert re.search(rf"^{step}\. Add any cross-cutting findings", body, re.M), (
        f"{name} does not number 'Add any cross-cutting findings' as step "
        f"{step}, but _SYNTHESIS_CROSS_CUTTING_STEP tells the guard it does."
    )
