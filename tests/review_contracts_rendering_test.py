"""Cross-file contract tests for prompt rendering — every template renders through
its builder with no placeholder left, the output-block contract, shared section
names and prompt budget accounting."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
TEMPLATE_DIR = LIB_DIR / "review-templates"

# Insert lib dir so we can import the review modules directly
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from conftest import make_ctx, model_budget_bytes  # noqa: E402

import agent.templates  # noqa: E402
import agent.invoke  # noqa: E402
import fix.ci  # noqa: E402
import fix.comments  # noqa: E402
import fix.engine  # noqa: E402
import fix.tracking  # noqa: E402
import fix.types  # noqa: E402
import fix.verify  # noqa: E402
import rebase.prepush  # noqa: E402
from agent.registry import PHASES, REVIEW_PHASES  # noqa: E402
from core.phases import Mode, Phase, PhaseShape  # noqa: E402
import review.fix  # noqa: E402
import review.prompt  # noqa: E402
import review.registry  # noqa: E402
import review.types  # noqa: E402
from gh.types import PRContext, PRMetadata  # noqa: E402
import pr.ci_failures  # noqa: E402
from pr.ci_report import CIReport  # noqa: E402
from pr.state import PRIdentity, PRState  # noqa: E402
from pr.thread_models import PRReport  # noqa: E402
from pr.triage_round import TriagedRound  # noqa: E402
from review.types import PreflightData, ReviewJob  # noqa: E402

from review_contracts_support import _template_files

# The model every phase resolves to here, and the ceiling it buys.
MAX_PROMPT_BYTES = model_budget_bytes()


def _extract_template_vars(template_path: Path) -> set[str]:
    """Extract ${var} placeholder names from a template file."""
    content = template_path.read_text()
    return set(re.findall(r"\$\{(\w+)\}", content))


# ── 4. Template rendering contracts ──────────────────────────────────────────
#
# These render every template the way production does — through build_prompt or
# through the script's own render function — rather than scraping handler source
# for string literals. A placeholder that never gets a value survives
# safe_substitute as a literal `${var}`, so rendering is the only way to see it.


def _make_review_job(**overrides) -> ReviewJob:
    """A ReviewJob with every optional section populated."""
    preflight = PreflightData(
        diff=(
            "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        ),
        commit_log="abc1234 feat: stuff",
        file_contents={"a.py": "print(1)\n"},
        file_permissions={"a.py": "100644"},
        instructions_md="# Project",
        architecture_md="# Architecture",
        omitted_files=["vendor/x.go"],
        delta_diff="diff --git a/a.py b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
        delta_commit_log="abc1234 feat: stuff",
        delta_files=["a.py"],
        prior_head_sha="0000000",
    )
    pr = PRMetadata(
        title="feat: thing", body="Body", head="user/feat/thing", base="main",
        head_sha="abc1234", additions=10, deletions=5, changed_files=1,
        files=[{"path": "a.py", "additions": 10, "deletions": 5}],
    )
    defaults = dict(
        repo="owner/repo", pr_number="1", pr=pr,
        ctx=PRContext(commits="abc1234 feat: stuff"),
        wt_path="/tmp/wt", review_file="/tmp/reviews/review.md",
        session_log="/tmp/reviews/session.jsonl",
        issue_link="#42", issue_context="Issue body",
        generator_version="test", preflight=preflight,
        viewer_role="OWNER",
    )
    defaults.update(overrides)
    return ReviewJob(**defaults)


# Extra kwargs each prompt needs, keyed by the (phase, mode) pair `build_prompt`
# is called with. A phase whose two modes render different templates appears
# twice; one that renders the same template either way appears once. No entry
# names an output path — the phase spec answers that, and a test supplying one
# would only be checking the value it handed in.
_BUILD_PROMPT_EXTRAS = {
    (Phase.SINGLE, Mode.PR): {},
    (Phase.SINGLE, Mode.SELF): {},
    (Phase.HOLISTIC, Mode.PR): {},
    (Phase.SCOUT, Mode.PR): {},
    (Phase.DISPROVE, Mode.PR): {"review_content": "- [M1] something"},
    (Phase.GROUP, Mode.PR): {
        "group_idx": 1, "group_count": 2, "group_name": "core",
        "group_files_formatted": "- a.py",
        "group_file_paths": ["a.py"],
    },
    (Phase.SYNTHESIS, Mode.PR): {
        "group_count": 2, "merged_content": "## Must fix\n",
    },
    (Phase.SYNTHESIS, Mode.SELF): {
        "group_count": 2, "merged_content": "## Must fix\n",
    },
}


def _template_of(key: tuple[Phase, Mode]) -> str:
    """The template a `(phase, mode)` key renders — also its parametrize id."""
    phase, mode = key
    return PHASES[phase].template_for(mode)


def _render_via_build_prompt(
    key: tuple[Phase, Mode], job: ReviewJob | None = None,
) -> str:
    phase, mode = key
    return review.registry.build_prompt(
        phase, job or _make_review_job(mode=mode), max_turns=15,
        **_BUILD_PROMPT_EXTRAS[key],
    )


def _render_adapter(adapter) -> str:
    """Render a fix template the way `fix.engine` renders it for a real pass.

    Going through the engine rather than restating the substitution keeps this
    honest about what an agent is actually handed: a placeholder the engine
    stopped supplying would show up here as unsubstituted, not as a passing
    test against a call nobody makes.
    """
    adapter.tracking_path.parent.mkdir(parents=True, exist_ok=True)
    adapter.tracking_path.write_text("- [ ] fixed\n")
    return fix.engine._prompt(adapter, 15)


def _render_fix_ci(wt_path) -> str:
    ctx = make_ctx(repo="owner/repo", branch="user/feat/thing",
                   worktree_root=wt_path, target_dir=wt_path)
    failure = pr.ci_failures.FailureItem(
        id="build-1", annotation="test failed", file=None, line=None,
        diagnosis=None, fix_sha=None, outcome=None, headline="test failed",
    )
    return _render_adapter(fix.ci.CIFixAdapter(
        CIReport(
            repo="owner/repo", branch="user/feat/thing", pr_number=42,
            run_id=100, run_ids=[100], run_number=1, head_sha="abc123",
            conclusion="failure", behind_main=0,
            failures={"build": pr.ci_failures.FailureGroup(
                job="build", kind=pr.ci_failures.FailureKind.BUILD, items=(failure,),
            )},
            progression={}, resolved_since_prior=[],
        ), ctx,
        PRState(identity=PRIdentity(
            repo="owner/repo", branch="user/feat/thing", pr_number=42,
            head_sha="abc123", worktree_root=str(wt_path),
        )),
    ))


def _render_fix_comments(wt_path) -> str:
    ctx = make_ctx(repo="owner/repo", branch="user/feat/thing",
                   pr_number=1, worktree_root=wt_path, target_dir=wt_path)
    adapter = fix.comments.CommentFixAdapter(
        PRReport(repo="owner/repo", pr_number=1), ctx, wt_path,
        TriagedRound(),
    )
    # The default-branch checkout is a fetch and a reset against a second
    # worktree; a render has no business making either.
    adapter.__dict__["main_wt"] = None
    return _render_adapter(adapter)


def _render_verify_fixes(wt_path) -> str:
    """Render the verify gate's prompt the way `fix.verify.run` renders it.

    Driven through the real runner with the agent call stubbed out, for the
    reason `_render_adapter` gives: a placeholder the runner stopped supplying
    has to show up here rather than pass against a call nobody makes.
    """
    ctx = make_ctx(repo="owner/repo", branch="user/feat/thing",
                   pr_number=1, worktree_root=wt_path, target_dir=wt_path)
    adapter = fix.comments.CommentFixAdapter(
        PRReport(repo="owner/repo", pr_number=1), ctx, wt_path,
        TriagedRound(),
    )
    adapter.__dict__["main_wt"] = None

    rendered = {}

    def capture(_phase, prompt, **_kwargs):
        rendered["prompt"] = prompt
        return agent.invoke.FixResult(0, None)

    with patch.object(agent.invoke, "run_fix", side_effect=capture):
        fix.verify.run(
            Phase.COMMENTS_VERIFY, "",
            items=[fix.types.FixItem(id="t1", file="a.py", line=2, label="x")],
            adapter=adapter,
        )
    return rendered["prompt"]


def _render_fix_findings(wt_path) -> str:
    job = _make_review_job(
        wt_path=str(wt_path),
        review_file=str(wt_path / "reviews" / "review.md"),
    )
    finding = review.types.Finding(
        id="M1", severity=review.types.SEVERITY_MUST, seq=1,
        path="a.py", line=3, end_line=None, body="the guard is missing",
    )
    return _render_adapter(review.fix.ReviewFixAdapter(job, [finding]))


def _render_fix_prepush(wt_path) -> str:
    """Render the pre-push repair pass's prompt through the real engine.

    The one fix template with no substitution test of its own until now. It
    renders through `fix.engine._prompt` like the other three, so the engine's
    own placeholders were covered by them — but anything this template names
    that the others do not was held by nothing.
    """
    return _render_adapter(rebase.prepush.PrePushFixAdapter(
        str(wt_path), ["server.go"], "gofmt: server.go needs formatting",
        # The lease the refused push carried; this renders a prompt and never
        # pushes, so the value only has to be the shape the adapter stores.
        args=("--force-with-lease=refs/heads/user/feat/thing:abc123",),
        repo="owner/repo", branch="user/feat/thing",
    ))


# Every fix template, keyed the way the parametrized contracts below name them.
# One list, so a fourth domain adopting the engine is added to the contracts by
# adding its renderer here rather than to each test in turn.
_FIX_RENDERERS = {
    "ci": _render_fix_ci,
    "comments": _render_fix_comments,
    "findings": _render_fix_findings,
    "prepush": _render_fix_prepush,
}

# Every template a fix-shaped agent is handed, including the verify gate's.
# Wider than `_FIX_RENDERERS` because the gate does not share two of the four
# contracts those renderers are held to: it edits nothing, so it carries no
# generated-artifact block, and it answers in `VERIFY_BOXES` rather than the fix
# pass's vocabulary. The contracts it does share — no Write-tool mandate, and a
# worktree block — apply for exactly the reasons they apply to the others, so it
# is held to those here rather than left outside the check because the wider set
# did not fit.
_AGENT_RENDERERS = _FIX_RENDERERS | {
    "verify": _render_verify_fixes,
}


def _make_common_sections() -> review.prompt.CommonSections:
    return review.prompt.CommonSections(
        **{name: "" for name in review.prompt.COMMON_SECTION_NAMES},
        budget_bytes=MAX_PROMPT_BYTES,
    )


def _unsubstituted(rendered: str) -> list[str]:
    return sorted(set(re.findall(r"\$\{(\w+)\}", rendered)))


class TestPromptBuilderRegistry:
    """`review.registry`'s table and the phase registry name the same phases.

    Keying the table by `Phase` is what makes this checkable at all: while the
    builders were keyed by template filename the two tables shared no name, so
    a phase could name a template and reach `build_prompt` with nothing behind
    it.
    """

    def test_every_agent_shaped_review_phase_has_a_builder(self):
        expected = {
            phase for phase in REVIEW_PHASES
            if PHASES[phase].shape is PhaseShape.AGENT
        }
        assert set(review.registry.registered()) == expected

    def test_a_phase_with_no_builder_is_refused(self):
        """The fix pass is a review phase, but `fix.engine` builds its prompt."""
        with pytest.raises(ValueError, match="renders no review prompt"):
            review.registry.build_prompt(Phase.FIX, _make_review_job(), max_turns=15)

    def test_every_builder_is_reached_by_the_extras_table(self):
        """Coverage below is per (phase, mode), so no builder goes unrendered."""
        assert {phase for phase, _ in _BUILD_PROMPT_EXTRAS} == set(
            review.registry.registered(),
        )


class TestTemplateRendering:
    """Every template renders with all placeholders substituted."""

    @pytest.mark.parametrize(
        "key", sorted(_BUILD_PROMPT_EXTRAS), ids=_template_of,
    )
    def test_build_prompt_templates_fully_substituted(self, key):
        rendered = _render_via_build_prompt(key)
        left = _unsubstituted(rendered)
        assert not left, (
            f"{_template_of(key)} rendered with unsubstituted placeholders: "
            + ", ".join(f"${{{v}}}" for v in left)
        )

    def test_fix_ci_template_fully_substituted(self, tmp_path):
        left = _unsubstituted(_render_fix_ci(tmp_path))
        assert not left, f"fix-ci.md left: {left}"

    def test_fix_comments_template_fully_substituted(self, tmp_path):
        left = _unsubstituted(_render_fix_comments(tmp_path))
        assert not left, f"fix-comments.md left: {left}"

    def test_fix_findings_template_fully_substituted(self, tmp_path):
        left = _unsubstituted(_render_fix_findings(tmp_path))
        assert not left, f"fix-findings.md left: {left}"

    def test_verify_fixes_template_fully_substituted(self, tmp_path):
        left = _unsubstituted(_render_verify_fixes(tmp_path))
        assert not left, f"verify-fixes.md left: {left}"

    # Closes a pre-existing coverage gap: the template it renders was already
    # fully substituted before this change added a placeholder to it.
    # passes-at-base: the gap it closes is coverage, not behaviour
    def test_fix_prepush_template_fully_substituted(self, tmp_path):
        left = _unsubstituted(_render_fix_prepush(tmp_path))
        assert not left, f"fix-prepush.md left: {left}"

    def test_the_comments_adapter_declares_the_gate_s_own_phase(self):
        """The one domain that runs a gate must name the gate's phase.

        Without it the engine falls back to the fix pass's phase, and the gate
        is handed `fix-comments.md` — a prompt instructing it to edit source,
        which `verify-fixes.md` forbids in as many words. The fallback is for a
        domain with no gate, so nothing else catches this.
        """
        phase = fix.comments.CommentFixAdapter.verify_phase
        assert phase is Phase.COMMENTS_VERIFY
        assert PHASES[phase].template_for() == "verify-fixes.md"

    def test_the_gate_is_told_to_check_the_claim_not_trust_it(self, tmp_path):
        """The claim is the fix agent's own words about its own work.

        Handing it over without telling the gate to falsify it makes "run the
        named test, it is green, tick verified" the cheapest path — which is
        the trap the rest of this prompt exists to close, re-entering through
        the front door.
        """
        text = " ".join(_render_verify_fixes(tmp_path).split())

        assert "The claim the fix pass made" in text
        assert "Does the named test exist" in text
        assert "Would it have failed before the change" in text
        # Provenance: the gate is told who wrote the claim and when.
        assert "written by the agent whose work you are checking" in text
        # And that a green named test does not by itself settle the verdict.
        assert "not by itself a **verified**" in text

    def test_a_claim_nobody_made_is_never_broken_on_its_own(self, tmp_path):
        """Absence of evidence caps at `not verified`.

        Without this the claim check makes the gate *more* likely to demote a
        good fix, which costs the operator real work — the opposite of what
        asking for evidence was for.
        """
        text = " ".join(_render_verify_fixes(tmp_path).split())
        assert "It is never **broken** on its own" in text

    def test_every_template_is_covered(self):
        """A new template must be added to this file's render coverage."""
        covered = {_template_of(key) for key in _BUILD_PROMPT_EXTRAS} | {
            PHASES[Phase.FIX].template_for(),
            PHASES[Phase.CI_FIX].template_for(),
            PHASES[Phase.COMMENTS_FIX].template_for(),
            PHASES[Phase.PREPUSH_FIX].template_for(),
            PHASES[Phase.COMMENTS_VERIFY].template_for(),
        }
        uncovered = sorted(
            name for name in _template_files() - covered
            if _extract_template_vars(TEMPLATE_DIR / name)
        )
        assert not uncovered, (
            "Templates with ${var} placeholders but no render coverage: "
            + ", ".join(uncovered)
        )


class TestOutputBlockContract:
    """Agents run under `claude --bare`, which has no Write tool."""

    # Phrasings that would tell an agent to use the unavailable Write tool.
    _WRITE_MANDATE = re.compile(
        r"(use|using|with|be)\s+the\s+Write\s+tool|Write\s+tool\s+to\s+(create|write|save)",
        re.IGNORECASE,
    )

    @pytest.mark.parametrize(
        "key", sorted(_BUILD_PROMPT_EXTRAS), ids=_template_of,
    )
    def test_no_write_tool_mandate(self, key):
        self._assert_no_mandate(_template_of(key), _render_via_build_prompt(key))

    @pytest.mark.parametrize("render", sorted(_AGENT_RENDERERS))
    def test_fix_templates_have_no_write_tool_mandate(self, render, tmp_path):
        self._assert_no_mandate(render, _AGENT_RENDERERS[render](tmp_path))

    def _assert_no_mandate(self, label, rendered):
        match = self._WRITE_MANDATE.search(rendered)
        assert not match, (
            f"{label} tells the agent to use the Write tool "
            f"({match.group(0)!r}) — it does not exist under --bare"
        )

    # ((phase, mode), output path, stdout_warning) — the paths are spelled out
    # rather than asked of `phase_output_path`, so a spec that renames an
    # artifact is caught here instead of agreeing with itself. `build_prompt`
    # derives them from the same `review_file` every job here carries.
    _OUTPUT_BLOCKS = [
        ((Phase.HOLISTIC, Mode.PR), "/tmp/reviews/holistic.md", False),
        ((Phase.SCOUT, Mode.PR), "/tmp/reviews/scout.md", False),
        ((Phase.DISPROVE, Mode.PR), "/tmp/reviews/disprove.md", False),
        ((Phase.GROUP, Mode.PR), "/tmp/reviews/group-1.md", False),
        ((Phase.SYNTHESIS, Mode.PR), "/tmp/reviews/review.md", False),
        ((Phase.SYNTHESIS, Mode.SELF), "/tmp/reviews/review.md", False),
        ((Phase.SINGLE, Mode.PR), "/tmp/reviews/review.md", True),
        ((Phase.SINGLE, Mode.SELF), "/tmp/reviews/review.md", True),
    ]

    @pytest.mark.parametrize(
        "key, output_path, stdout_warning", _OUTPUT_BLOCKS,
        ids=[_template_of(k) for k, _, _ in _OUTPUT_BLOCKS],
    )
    def test_output_block_rendered_verbatim(self, key, output_path, stdout_warning):
        rendered = _render_via_build_prompt(key)
        expected = agent.templates.build_output_block(
            output_path, stdout_warning=stdout_warning,
        )
        assert expected in rendered

    def test_the_rendered_recipe_follows_the_selected_backend(self, monkeypatch):
        """A prompt built under Pi must not carry Claude's Edit recipe.

        The regression this closes: `build_output_block` was written for
        `claude --bare` and hardcoded its recipe, so every Pi review was told
        to Edit with an empty `old_string` and that the Write tool did not
        exist. Pi's edit takes `edits[].oldText` and rejects an empty one, and
        Pi does have `write` — so the instruction could not be followed and the
        fallbacks were forbidden. Agents spent their turns and wrote nothing.
        """
        monkeypatch.setenv("AI_BACKEND", "pi")
        rendered = _render_via_build_prompt((Phase.SINGLE, Mode.SELF))
        assert "old_string" not in rendered
        assert "The Write tool is NOT available" not in rendered
        assert "`write` tool" in rendered

    def test_every_output_writing_template_is_checked(self):
        checked = {key for key, _, _ in self._OUTPUT_BLOCKS}
        expected = {
            key for key in _BUILD_PROMPT_EXTRAS
            if "output_block" in _extract_template_vars(
                TEMPLATE_DIR / _template_of(key),
            )
        }
        assert expected == checked

    @pytest.mark.parametrize("render", sorted(_AGENT_RENDERERS))
    def test_fix_templates_share_the_worktree_block(self, render, tmp_path):
        rendered = _AGENT_RENDERERS[render](tmp_path)
        assert agent.templates.build_worktree_block(str(tmp_path)) in rendered

    @pytest.mark.parametrize("render", sorted(_FIX_RENDERERS))
    def test_fix_templates_share_the_generated_block(self, render, tmp_path):
        """Any fix pass can edit a source whose artifact then needs rebuilding.

        Not a property of the domain — a CI fix, a comment fix and a finding fix
        all reach `lib/` docstrings and `.src` documents — so every template
        carries it and none of them words it for itself.
        """
        rendered = _FIX_RENDERERS[render](tmp_path)
        assert agent.templates.GENERATED_BLOCK in rendered

    @pytest.mark.parametrize("render", sorted(_FIX_RENDERERS))
    def test_fix_templates_share_the_role_block(self, render, tmp_path):
        """Every fix agent runs under guidance written for the operator.

        `--add-dir` restores CLAUDE.md discovery that `--bare` skips, so the
        operator's whole rule set arrives on every invocation — including the
        parts about creating PRs, pushing, and filing issues, none of which
        this agent can do. The block scopes that out, and it is one fact about
        the role rather than four domain-specific paragraphs.
        """
        rendered = _FIX_RENDERERS[render](tmp_path)
        assert agent.templates.ROLE_BLOCK in rendered

    def test_fix_and_verify_templates_forbid_unscoped_runners(self, tmp_path):
        """A phase whose budget is turns must not spend them on the pre-push gate."""
        findings = _render_fix_findings(tmp_path)
        verify = _render_verify_fixes(tmp_path)
        for text in (findings, verify):
            assert "invoke it directly (`pytest tests/foo.py`)" in text
            assert "bin/local/run-tests" in text
            assert "validate-all" in text
        assert "Run the project's own checks" not in verify

    def test_the_verify_template_does_not_claim_the_editing_role(self):
        """The gate is read-only and says so itself in stricter terms.

        Handing it a block that opens "you are editing files in a worktree"
        would contradict the one instruction that matters most there — that it
        checks work rather than continuing it.
        """
        text = (TEMPLATE_DIR / "verify-fixes.md").read_text()
        assert "${role_block}" not in text
        assert "Do not edit source files" in text

    # The two domains whose items are somebody's claim about the code: a review
    # finding and a PR review comment. CI and pre-push are excluded because
    # their item is a check that already failed — there is no premise to
    # disprove, and telling those passes to doubt the failure is how a real red
    # build gets declined.
    _CLAIM_DOMAINS = ("comments", "findings")

    @pytest.mark.parametrize("render", _CLAIM_DOMAINS)
    def test_a_claim_domain_is_told_to_disprove_before_it_edits(
        self, render, tmp_path,
    ):
        """A false finding fixed becomes dead code with a vacuous test beside it.

        That is not hypothetical: a CRLF finding that was wrong got a guard and
        a test which passed with or without it. The disprove gate ahead of the
        fix pass does not cover this — it is skipped at low effort and below
        three must/should findings, and it never runs at all for comments.
        """
        text = " ".join(_FIX_RENDERERS[render](tmp_path).split())

        # The ask itself, and that it comes before the edit rather than after.
        assert "before you" in text
        assert "not a fact about it" in text
        assert "Try to make the case that the" in text
        # The failure mode it exists to prevent, named so the agent can see it.
        assert "dead code" in text
        # And the cap on it: doubt is not disproof, or a fix pass talks itself
        # out of real work.
        assert "Default to fixing when you cannot tell" in text

    @pytest.mark.parametrize("render", sorted(set(_FIX_RENDERERS) - set(_CLAIM_DOMAINS)))
    def test_a_check_domain_is_not_told_to_doubt_its_failure(
        self, render, tmp_path,
    ):
        """CI and pre-push are handed a check that failed, not a claim.

        Their oracle is the red build. A pass told to disprove it declines real
        failures, which is the opposite defect from the one the claim domains
        have.
        """
        text = " ".join(_FIX_RENDERERS[render](tmp_path).split())
        assert "Try to make the case that the" not in text

    @pytest.mark.parametrize("render", sorted(_FIX_RENDERERS))
    def test_fix_templates_explain_every_box_the_checklist_offers(
        self, render, tmp_path,
    ):
        """The boxes are `fix.tracking`'s; the prose explaining them is per-domain.

        Every template spells out the same three answers in its own words, so a
        box renamed or added in `fix.tracking` leaves prose behind that describes
        a checklist the agent is not looking at. No template has to say it the
        same way — each only has to still be talking about all of them.
        """
        task = _FIX_RENDERERS[render](tmp_path).split("## Task", 1)[1]
        for box in fix.tracking._BOXES:
            why = (
                f" — {fix.tracking._WHY}"
                if box.outcome in fix.tracking._REASONED
                else ""
            )
            assert f"`- [x] {box.label}{why}`" in task, box.label


class TestSharedSectionNames:
    """`shared()` takes bare strings — catch typos statically, not at call time."""

    def _shared_call_args(self) -> list[tuple[int, str]]:
        tree = ast.parse((LIB_DIR / "review" / "prompt.py").read_text())
        return [
            (node.lineno, arg.value)
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "shared"
            for arg in node.args
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str)
        ]

    def test_handlers_only_share_real_fields(self):
        calls = self._shared_call_args()
        assert calls, "found no shared() calls to check — did the API change?"
        bad = [
            f"review/prompt.py:{lineno} {name!r}"
            for lineno, name in calls
            if name not in review.prompt.COMMON_SECTION_NAMES
        ]
        assert not bad, "shared() called with non-CommonSections names: " + ", ".join(bad)

    def test_unknown_name_raises_with_the_valid_set(self):
        builder = review.prompt.PromptBuilder(_make_common_sections())
        with pytest.raises(KeyError, match="pr_haeder"):
            builder.shared("pr_haeder")

    def test_every_common_field_is_reachable(self):
        """A field no handler shares is dead weight on every prompt build."""
        shared_names = {name for _, name in self._shared_call_args()}
        unused = sorted(review.prompt.COMMON_SECTION_NAMES - shared_names)
        assert not unused, f"CommonSections fields no handler uses: {unused}"


class TestPromptBudgetAccounting:
    """Sections registered on the builder count against the diff budget.

    The group handler once computed its budget before registering
    `project_context` and never counted `group_files_formatted` at all, so those
    bytes landed on top of a diff already sized to fill the budget.
    """

    # Enough per-file diffs to overflow the budget, so truncation is granular
    # and the prompt sits right at the limit rather than dropping one big blob.
    _PATHS = [f"f{i:03d}.py" for i in range(60)]

    def _group_prompt(self, files_formatted: str) -> str:
        job = _make_review_job()
        job.pr.files = [
            {"path": p, "additions": 1, "deletions": 0} for p in self._PATHS
        ]
        job.preflight.diff = "".join(
            f"diff --git a/{p} b/{p}\n--- a/{p}\n+++ b/{p}\n@@ -1 +1 @@\n"
            + "+xxxxxxxxxxxxxxxxxxxx\n" * 500
            for p in self._PATHS
        )
        job.preflight.file_contents = {}
        extra = dict(_BUILD_PROMPT_EXTRAS[(Phase.GROUP, Mode.PR)])
        extra["group_file_paths"] = list(self._PATHS)
        extra["group_files_formatted"] = files_formatted
        return review.registry.build_prompt(
            Phase.GROUP, job, max_turns=15, **extra,
        )

    def test_large_section_shrinks_the_diff_instead_of_the_prompt_budget(self):
        filler = "- filler.py\n" * 2000
        small = self._group_prompt("- a.py")
        large = self._group_prompt("- a.py\n" + filler)

        assert len(small.encode()) <= MAX_PROMPT_BYTES
        assert len(large.encode()) <= MAX_PROMPT_BYTES
        # The filler must come out of the diff's share, not on top of it.
        growth = len(large.encode()) - len(small.encode())
        assert growth < len(filler.encode()) // 2, (
            f"adding {len(filler)}B to group_files_formatted grew the prompt by "
            f"{growth}B — the section is not counted against the diff budget"
        )
