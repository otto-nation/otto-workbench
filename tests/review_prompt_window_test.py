"""review.budget: the budget a model's context window buys, and where the window comes from."""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.budget import prompt_budget_bytes
from core.phases import Phase

from conftest import TEST_MODEL, _REAL_RUN_PI_LIST_MODELS

from review_prompt_support import _make_preflight, _make_job


# ── The ceiling is the model's, not a constant ──────────────────────────────


class TestTheBudgetComesFromTheModel:
    """A byte ceiling that names no model bounds nothing it can be held to.

    The budget declared a ceiling in tokens and enforced it in bytes at an
    assumed four bytes per token. Real prompts measure 2.23–2.89 on
    `claude-sonnet-5`, so the ceiling was ~1.8x more permissive than it read,
    and `120_000` corresponded to no model's window at all.
    """

    def test_a_narrow_window_buys_a_smaller_budget(self):
        """The case the fused constant got wrong.

        Every phase resolving to a 1M-window model is a configuration, not a
        property of the design — a 200k model has to budget below it, and a
        single constant cannot say both.
        """
        wide = prompt_budget_bytes("claude-sonnet-5")
        narrow = prompt_budget_bytes("claude-sonnet-4-6")
        assert narrow < wide

    def test_a_wide_window_is_capped_by_what_a_review_will_spend(self):
        """Capability and willingness to pay are different bounds.

        A 1M-token window permits a 1.8MB prompt, which is no cheaper for being
        permitted. The budget holds spend where it was rather than quadrupling
        it because the window allows it.
        """
        from review.budget import MAX_SPEND_BYTES

        assert prompt_budget_bytes("claude-sonnet-5") <= MAX_SPEND_BYTES

    def test_the_budget_leaves_room_for_the_reply_and_the_unseen_overhead(self):
        """The window is not all the prompt's to spend.

        The reply comes out of it, and so do the system prompt and tool schemas
        that `claude -p` assembles where nothing here can measure them.
        """
        from review.budget import (
            COMPLETION_RESERVE_TOKENS, MODEL_CONTEXT_TOKENS,
            OVERHEAD_RESERVE_TOKENS, prompt_budget_tokens,
        )

        model = "claude-sonnet-4-6"
        reserved = COMPLETION_RESERVE_TOKENS + OVERHEAD_RESERVE_TOKENS
        assert prompt_budget_tokens(model) == MODEL_CONTEXT_TOKENS[model] - reserved

    def test_pi_reserves_less_overhead_than_claude(self, monkeypatch):
        from core.phases import Backend
        from review.budget import (
            OVERHEAD_RESERVE_TOKENS, PI_OVERHEAD_RESERVE_TOKENS,
            overhead_reserve_tokens, prompt_budget_tokens,
        )

        assert overhead_reserve_tokens(Backend.PI) == PI_OVERHEAD_RESERVE_TOKENS
        assert overhead_reserve_tokens(Backend.CLAUDE) == OVERHEAD_RESERVE_TOKENS
        monkeypatch.setattr("agent.backend.selected_backend", lambda: None)
        assert overhead_reserve_tokens() == OVERHEAD_RESERVE_TOKENS
        model = "claude-sonnet-4-6"
        assert prompt_budget_tokens(model, Backend.PI) > prompt_budget_tokens(
            model, Backend.CLAUDE,
        )

    def test_an_unresolved_alias_takes_its_tier_floor(self):
        """The ordinary first-party-API setup, and not an error.

        `phase_model` returns the literal string "sonnet" when
        ANTHROPIC_DEFAULT_SONNET_MODEL is unset, which is what a machine on the
        first-party API looks like rather than a misconfiguration. Refusing
        would take out every phase at once on those machines; budgeting
        against the tier's narrowest window is safe whichever concrete model
        it turns out to name.
        """
        from review.budget import ALIAS_FLOOR_TOKENS, model_window_tokens

        assert model_window_tokens("sonnet") == ALIAS_FLOOR_TOKENS
        assert prompt_budget_bytes("sonnet") == prompt_budget_bytes("claude-sonnet-4-6")

    def test_the_ladder_plans_below_the_ceiling_it_is_refused_at(self):
        """The reserve only holds bytes back if the ladder never sees them.

        Subtracting it from the ceiling alone and then handing the ladder that
        same figure spends it: the ladder fills its sections to whatever target
        it is given, and the markup the render adds lands on top. A group
        prompt planned to exactly its allowance rendered 2,759 bytes over and
        was refused.
        """
        from review.budget import RENDER_MARKUP_RESERVE_BYTES, ladder_target_bytes

        for model in ("claude-sonnet-5", "sonnet"):
            gap = prompt_budget_bytes(model) - ladder_target_bytes(model)
            assert gap == RENDER_MARKUP_RESERVE_BYTES, model

    def test_a_window_its_reserves_exhaust_is_refused(self, monkeypatch):
        """A negative budget would be absorbed rather than noticed.

        `_fit_budget` guards every subtraction with `max(0, ...)`, so a window
        smaller than the reserves would not crash — every phase would quietly
        refuse every prompt, and the cause would be a table entry nobody would
        think to look at.
        """
        import review.budget
        from review.budget import UnknownModelWindow

        monkeypatch.setitem(review.budget.MODEL_CONTEXT_TOKENS, "tiny", 50_000)
        with pytest.raises(UnknownModelWindow, match="exhaust"):
            prompt_budget_bytes("tiny")

    def test_the_alias_floor_is_never_more_generous_than_a_real_window(self):
        """Guessing wide is the expensive direction.

        The floor stands in for a model nobody has named, so it has to be no
        larger than the narrowest window it could turn out to be.
        """
        from review.budget import ALIAS_FLOOR_TOKENS, MODEL_CONTEXT_TOKENS

        assert ALIAS_FLOOR_TOKENS <= min(MODEL_CONTEXT_TOKENS.values())

    def test_an_unknown_model_names_the_ones_on_record(self):
        from review.budget import UnknownModelWindow

        with pytest.raises(UnknownModelWindow, match="claude-sonnet-5") as caught:
            prompt_budget_bytes("gpt-5")
        assert "catalogue" in str(caught.value)

    def test_the_record_says_which_model_the_budget_came_from(
        self, tmp_path, monkeypatch,
    ):
        """A density without its tokenizer is not interpretable.

        The tokenizer is generation-specific — sonnet-5 counts the same text
        ~27% denser than sonnet-4-5 — so a budget recorded without its model
        cannot be compared against another run's.

        The alias is resolved explicitly rather than left to the environment:
        a developer's shell exports `ANTHROPIC_DEFAULT_SONNET_MODEL` and CI
        does not, so reading it would assert a different model in each place.
        """
        from review.registry import build_prompt

        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", TEST_MODEL)
        job = _make_job(_make_preflight())
        job.review_file = str(tmp_path / "review.md")
        build_prompt(Phase.SCOUT, job, max_turns=10)

        record = json.loads((tmp_path / "prompt-stats.json").read_text())[-1]
        assert record["budget_model"] == TEST_MODEL
        assert record["budget_window_tokens"] == 1_000_000
        assert record["budget_bytes"] == prompt_budget_bytes(TEST_MODEL)

    def test_an_alias_records_the_window_it_actually_budgeted_against(
        self, tmp_path, monkeypatch,
    ):
        """The alias path is ordinary, so its record has to read as ordinary.

        A window of 0 beside a nonzero budget reads as a bug in the budget
        rather than as the documented tier-floor path, which is exactly the
        diagnostic this field exists to serve.
        """
        from review.budget import ALIAS_FLOOR_TOKENS
        from review.registry import build_prompt

        monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
        job = _make_job(_make_preflight())
        job.review_file = str(tmp_path / "review.md")
        build_prompt(Phase.SCOUT, job, max_turns=10)

        record = json.loads((tmp_path / "prompt-stats.json").read_text())[-1]
        assert record["budget_model"] == "sonnet"
        assert record["budget_window_tokens"] == ALIAS_FLOOR_TOKENS
        assert record["budget_bytes"] == prompt_budget_bytes("sonnet")

    def test_the_refusal_names_the_budget_it_was_measured_against(self):
        """An over-budget phase is skipped, so its message is the whole report.

        One recorded render in 1,897 exceeded the budget and the phase was
        dropped from the review; a message naming neither the model nor the
        ceiling leaves no way to tell a real overflow from a misconfigured
        window.
        """
        from review.prompt import PromptTooLarge

        exc = PromptTooLarge(
            "scout.md", 500_000, budget_bytes=464_000, model="claude-sonnet-4-6",
        )
        assert "claude-sonnet-4-6" in str(exc)
        assert exc.budget_bytes == 464_000
        assert exc.model == "claude-sonnet-4-6"


class TestTheWindowPrefersPisCatalogue:
    """The live provider catalogue is the source; the table is the fallback.

    `MODEL_CONTEXT_TOKENS` copies figures from `pi --list-models`. Reading the
    catalogue itself means a model pi already knows does not have to be copied
    here before a review can budget against it.
    """

    @pytest.fixture(autouse=True)
    def _clear_catalogue_cache(self):
        from review.budget import _pi_catalogue_windows

        _pi_catalogue_windows.cache_clear()
        yield
        _pi_catalogue_windows.cache_clear()

    def test_the_parser_reads_k_and_m_and_rounds_down(self):
        """Overestimating a window is the expensive direction, so floor."""
        from review.budget import _parse_context_tokens

        assert _parse_context_tokens("200K") == 200_000
        assert _parse_context_tokens("1M") == 1_000_000
        assert _parse_context_tokens("1.0M") == 1_000_000
        assert _parse_context_tokens("65.5K") == 65_500
        assert _parse_context_tokens("1.9") == 1
        assert _parse_context_tokens("context") is None

    def test_a_catalogue_hit_wins_over_the_table(self, monkeypatch):
        """A live figure is what the model will actually hold."""
        import review.budget
        from review.budget import MODEL_CONTEXT_TOKENS, model_window_tokens

        assert MODEL_CONTEXT_TOKENS["claude-sonnet-5"] == 1_000_000
        monkeypatch.setattr(review.budget, "_run_pi_list_models", lambda: (
            "provider model context max-out thinking images\n"
            "google-vertex-claude claude-sonnet-5 500K 128K yes yes\n"
        ))
        assert model_window_tokens("claude-sonnet-5") == 500_000

    def test_a_catalogue_miss_falls_back_to_the_table(self, monkeypatch):
        import review.budget
        from review.budget import MODEL_CONTEXT_TOKENS, model_window_tokens

        monkeypatch.setattr(review.budget, "_run_pi_list_models", lambda: (
            "provider model context max-out thinking images\n"
            "google-vertex gemini-2.5-flash 1.0M 65.5K yes yes\n"
        ))
        assert model_window_tokens("claude-sonnet-4-6") == MODEL_CONTEXT_TOKENS[
            "claude-sonnet-4-6"
        ]

    def test_an_unusable_catalogue_falls_back_to_the_table(self, monkeypatch):
        """pi absent, failing, or unparseable must not change today's fallback.

        The table and UnknownModelWindow are the contract when the catalogue
        cannot be read; raising would make an optional lookup a hard dependency.
        """
        import core.proc
        import review.budget
        from core.proc import CmdResult
        from review.budget import MODEL_CONTEXT_TOKENS, model_window_tokens

        model = "claude-sonnet-4-6"
        expected = MODEL_CONTEXT_TOKENS[model]
        monkeypatch.setattr(
            review.budget, "_run_pi_list_models", _REAL_RUN_PI_LIST_MODELS,
        )

        def missing(*_a, **_k):
            raise FileNotFoundError("pi")

        def not_executable(*_a, **_k):
            raise PermissionError("pi")

        for run in (
            missing,
            not_executable,
            lambda *_a, **_k: CmdResult(returncode=1),
            lambda *_a, **_k: CmdResult(returncode=0, stdout="not a table"),
            lambda *_a, **_k: CmdResult(returncode=0, stdout=""),
        ):
            monkeypatch.setattr(core.proc, "run", run)
            review.budget._pi_catalogue_windows.cache_clear()
            assert model_window_tokens(model) == expected

    def test_an_unknown_model_names_catalogue_models_too(self, monkeypatch):
        """A typo'd model's error should not undersell what's actually known.

        MODEL_CONTEXT_TOKENS alone is a narrower list than a readable
        catalogue usually offers; the message should name both.
        """
        import review.budget
        from review.budget import UnknownModelWindow, prompt_budget_bytes

        monkeypatch.setattr(review.budget, "_run_pi_list_models", lambda: (
            "provider model context max-out thinking images\n"
            "xai grok-4.6 120K 32K yes no\n"
        ))
        with pytest.raises(UnknownModelWindow) as caught:
            prompt_budget_bytes("gpt-5")
        assert "grok-4.6" in str(caught.value)

    def test_an_unreadable_catalogue_is_not_blamed_on_the_model(self):
        """With no catalogue (the autouse fixture's default), say it was unreadable."""
        from review.budget import UnknownModelWindow, prompt_budget_bytes

        with pytest.raises(UnknownModelWindow) as caught:
            prompt_budget_bytes("gpt-5")
        message = str(caught.value)
        assert "could not be read" in message
        assert "not in pi's provider catalogue" not in message

    def test_the_narrowest_window_wins_across_providers(self, monkeypatch):
        """The same id under two providers is only as wide as the smaller."""
        import review.budget
        from review.budget import model_window_tokens

        monkeypatch.setattr(review.budget, "_run_pi_list_models", lambda: (
            "provider model context max-out thinking images\n"
            "google-vertex-grok grok-4.6 120K 32K yes no\n"
            "xai grok-4.6 500K 500K yes yes\n"
        ))
        assert model_window_tokens("grok-4.6") == 120_000


class TestCollectionBudgetsForEveryPhase:
    """Collection runs once and every phase reads the result.

    So it has no single model to budget against, and the choice between them
    is not arbitrary: what fits the tightest-windowed phase fits all of them,
    while the widest would hand a phase more than its own model can hold.
    """

    def test_it_takes_the_tightest_phase_budget(self, monkeypatch):
        import review.budget

        # Patched on `review.budget`, which binds the name at import time —
        # patching `agent.phases` would leave this reading the real resolution
        # and asserting nothing.
        monkeypatch.setattr(
            review.budget, "collect_phase_models",
            lambda *_: {"claude-sonnet-5": [], "claude-sonnet-4-6": []},
        )
        # The ladder's target, since collection is sizing what the ladder will
        # later be handed rather than the ceiling it is refused at.
        assert review.budget.collection_budget_bytes() == (
            review.budget.ladder_target_bytes("claude-sonnet-4-6")
        )

    def test_it_resolves_against_the_worktree_it_is_given(self, monkeypatch):
        """Collection budgets to the same models the phases will run.

        A repo naming a model in its own `.workbench.yml` resolves it only when
        the worktree is passed down, so dropping it here would size collection
        against a ceiling no phase budgets to.
        """
        import review.budget

        seen = {}

        def _record(explicit, project_root=None):
            seen["explicit"], seen["root"] = explicit, project_root
            return {"claude-sonnet-5": []}

        monkeypatch.setattr(review.budget, "collect_phase_models", _record)
        review.budget.collection_budget_bytes("m", "/wt")
        assert seen == {"explicit": "m", "root": "/wt"}
