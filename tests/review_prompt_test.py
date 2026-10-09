"""Tests for review.prompt: shared bodies, the oversize refusal, and the record of a render."""

import json
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from review.budget import prompt_budget_bytes
from core.phases import Mode, Phase

from unittest.mock import patch

from review.prompt import build_common_sections, log_prompt_size
import review.registry
from conftest import TEST_MODEL

from review_prompt_support import MAX_PROMPT_BYTES, _make_preflight, _make_job


# ── Shared prompt bodies ────────────────────────────────────────────────────


class TestSharedPromptBodies:
    """The paired prompts must stay interchangeable apart from their variant."""

    def _vars(self, phase, output, mode=Mode.PR, **extra):
        job = _make_job(_make_preflight(), mode=mode)
        common = build_common_sections(job, max_turns=10, budget_bytes=MAX_PROMPT_BYTES)
        built = review.registry.for_phase(phase).build(job, common, extra, output)
        return built.builder.vars

    def test_scout_and_holistic_are_the_same_prompt(self):
        """One builder, two artifacts: the phase spec is the whole difference.

        The two are alternative first passes over identical inputs, so sharing
        the builder is what keeps them that way — a section added for one is
        added for both, and the file each writes is the spec's answer.
        """
        assert (
            review.registry.for_phase(Phase.HOLISTIC).build
            is review.registry.for_phase(Phase.SCOUT).build
        )
        holistic = self._vars(Phase.HOLISTIC, "/tmp/h.md")
        scout = self._vars(Phase.SCOUT, "/tmp/s.md")
        assert holistic.keys() == scout.keys()
        differing = [k for k in holistic if holistic[k] != scout[k]]
        assert differing == ["output_block"]

    def test_single_variants_differ_only_in_identity_and_the_verdict(self):
        pr = self._vars(Phase.SINGLE, "/tmp/r.md")
        self_ = self._vars(Phase.SINGLE, "/tmp/r.md", mode=Mode.SELF)
        assert set(pr) - set(self_) == {
            "pr_number", "reviews_section", "verdict_options",
        }
        assert set(self_) - set(pr) == {"branch_name"}
        # The re-review preamble is worded per mode, so it is expected to differ.
        common_keys = (set(pr) & set(self_)) - {"prior_section"}
        assert all(pr[k] == self_[k] for k in common_keys)

    def test_synthesis_variants_differ_only_in_identity_and_prior_reviews(self):
        shared = dict(group_count=2, merged_content="m", holistic_content="h")
        pr = self._vars(Phase.SYNTHESIS, "/tmp/r.md", **shared)
        self_ = self._vars(Phase.SYNTHESIS, "/tmp/r.md", mode=Mode.SELF, **shared)
        assert set(pr) - set(self_) == {"pr_number", "pr_title", "reviews_section"}
        assert set(self_) - set(pr) == {"branch_name"}
        # The execution-claim guard names the step that adds cross-cutting
        # findings, and the two templates number that step differently — 8 in
        # synthesis.md, 9 in self-review-synthesis.md. Expected to differ, and
        # held against the templates by
        # test_synthesis_cross_cutting_step_matches_its_template.
        common_keys = (set(pr) & set(self_)) - {"execution_claim_guard"}
        assert all(pr[k] == self_[k] for k in common_keys)
        assert pr["execution_claim_guard"] != self_["execution_claim_guard"]


# ── An over-budget prompt is refused, not logged past ───────────────────────


class TestBuildPromptRefusesAnOversizedPrompt:
    # instructions_md is fixed overhead — no lever reaches it — so one over the whole
    # budget puts the prompt past it whatever the ladder cuts. Sized and
    # asserted against the refusal ceiling rather than the ladder's target:
    # those differ by the render-markup reserve, and a prompt between them is
    # over the ladder's plan but not over the line `build_prompt` refuses at.
    CEILING = prompt_budget_bytes(TEST_MODEL)
    UNBUDGETABLE = "x" * (CEILING + 1000)

    @pytest.fixture(autouse=True)
    def _pin_the_alias(self, monkeypatch):
        """CI leaves ANTHROPIC_DEFAULT_SONNET_MODEL unset and a shell exports it.

        Unpinned, `CEILING` is this file's constant while the run resolves the
        bare alias to the tier floor, so the fixture is sized against one model
        and the assertion made against another.
        """
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", TEST_MODEL)

    def _job(self, tmp_path, **preflight):
        job = _make_job(_make_preflight(**preflight))
        job.review_file = str(tmp_path / "review.md")
        return job

    def test_a_prompt_over_the_budget_raises(self, tmp_path):
        from review.prompt import PromptTooLarge
        from review.registry import build_prompt

        job = self._job(tmp_path, instructions_md=self.UNBUDGETABLE)
        with pytest.raises(PromptTooLarge) as exc:
            build_prompt(Phase.SCOUT, job, max_turns=10)
        assert exc.value.prompt_bytes > self.CEILING

    def test_the_oversized_prompt_is_on_disk_to_look_at(self, tmp_path):
        """The stats are written before the raise, so the run is diagnosable."""
        from review.prompt import PromptTooLarge
        from review.registry import build_prompt

        job = self._job(tmp_path, instructions_md=self.UNBUDGETABLE)
        with pytest.raises(PromptTooLarge):
            build_prompt(Phase.SCOUT, job, max_turns=10)
        stats = json.loads((tmp_path / "prompt-stats.json").read_text())
        assert stats[-1]["prompt_bytes"] > self.CEILING
        assert (tmp_path / "prompt-scout.md").exists()

    def test_an_ordinary_prompt_still_renders(self, tmp_path):
        from review.registry import build_prompt

        prompt = build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        assert len(prompt.encode()) <= MAX_PROMPT_BYTES
        assert "Incremental review context" in prompt


# ── The budget accounting in the prompt stats ───────────────────────────────


class TestTheRecordAccountsForWhatItRendered:
    """`prompt-stats.json` is where an unmeasured section becomes findable.

    The first five real records paired the render against the diff's whole
    allowance and read −246KB, −226KB, −223KB, −244KB, −230KB — the budget's
    unspent room, reported as though the render had come in under an estimate.
    A metric whose ordinary value is a quarter-megabyte of slack cannot surface
    the few kilobytes of excess it exists to detect, so the sign is the thing
    these pin.
    """

    def _job(self, tmp_path):
        job = _make_job(_make_preflight())
        job.review_file = str(tmp_path / "review.md")
        return job

    def _stats(self, tmp_path):
        return json.loads((tmp_path / "prompt-stats.json").read_text())[-1]

    def test_the_record_names_the_allowance_and_what_it_accounted_for(self, tmp_path):
        review.registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        stats = self._stats(tmp_path)
        assert stats["allowance_bytes"] > 0
        assert stats["accounted_bytes"] > 0
        assert "unaccounted_bytes" in stats
        # The old pair is retired rather than redefined, so a reader can tell
        # the two shapes apart and drop the records whose residual was noise.
        assert "planned_bytes" not in stats
        assert "residual_bytes" not in stats

    def test_the_unaccounted_bytes_are_a_template_not_a_quarter_of_a_megabyte(
        self, tmp_path,
    ):
        review.registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        stats = self._stats(tmp_path)
        # A few KB of template text and block markup no lever sizes. The bound
        # is loose on purpose: the honest threshold is a question for the data.
        assert 0 <= stats["unaccounted_bytes"] < 50_000
        # The slack the retired field mistook for a residual is still visible,
        # under a name that says it is unspent allowance.
        assert stats["allowance_bytes"] - stats["accounted_bytes"] > 100_000

    def test_a_phase_with_no_budgeted_section_records_no_accounting(self, tmp_path):
        job = self._job(tmp_path)
        review.registry.build_prompt(
            Phase.DISPROVE, job, max_turns=10,
            extra={"findings_block": "- a finding"},
        )
        stats = self._stats(tmp_path)
        assert "accounted_bytes" not in stats
        assert "unaccounted_bytes" not in stats


# ── Token telemetry in the prompt stats ─────────────────────────────────────


class TestPromptTokenTelemetry:
    """What `prompt-stats.json` records about a prompt's real token cost.

    The counter is a network round trip, so it is patched here; what these
    hold is the contract around it — that it is off unless asked for, that an
    unavailable count leaves no trace rather than a wrong one, and that a count
    is never recorded without the tokenizer it was measured against.
    """

    def _job(self, tmp_path):
        job = _make_job(_make_preflight())
        job.review_file = str(tmp_path / "review.md")
        return job

    def _stats(self, tmp_path):
        return json.loads((tmp_path / "prompt-stats.json").read_text())[-1]

    def test_measured_by_default(self, tmp_path, monkeypatch):
        """A measurement nobody takes calibrates nothing.

        This shipped opt-in and was never switched on: 1,758 recorded renders
        carry no token count at all. The default is now on, so the density the
        budget reasons about comes from real reviews.
        """
        monkeypatch.delenv("WORKBENCH_AI_MEASURE_TOKENS", raising=False)
        with patch("review.prompt.count_tokens", return_value=1000) as counter:
            review.registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        counter.assert_called_once()
        assert self._stats(tmp_path)["prompt_tokens"] == 1000

    def test_zero_opts_out(self, tmp_path, monkeypatch):
        """The round trip is small but not free, and a run may decline it."""
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "0")
        with patch("review.prompt.count_tokens") as counter:
            review.registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        counter.assert_not_called()
        assert "prompt_tokens" not in self._stats(tmp_path)

    def test_records_count_model_and_density_when_measured(self, tmp_path, monkeypatch):
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=1000):
            review.registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        stats = self._stats(tmp_path)
        assert stats["prompt_tokens"] == 1000
        # A density without its tokenizer is not interpretable: sonnet-5 counts
        # the same text ~27% denser than sonnet-4-5.
        assert stats["token_model"]
        assert stats["bytes_per_token"] == round(stats["prompt_bytes"] / 1000, 3)

    def test_an_unavailable_count_records_nothing(self, tmp_path, monkeypatch):
        """None is not zero — a missing measurement must leave no density behind."""
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=None):
            review.registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        stats = self._stats(tmp_path)
        assert "prompt_tokens" not in stats
        assert "bytes_per_token" not in stats

    def test_a_zero_count_records_no_density(self, tmp_path, monkeypatch):
        """Zero is a count, but it is not a density — and must not divide."""
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=0):
            review.registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        stats = self._stats(tmp_path)
        assert stats["prompt_tokens"] == 0
        assert "bytes_per_token" not in stats

    def test_counts_against_the_model_the_phase_will_use(self, tmp_path, monkeypatch):
        """A count against another tokenizer is worse than no count at all."""
        monkeypatch.setenv("WORKBENCH_AI_MEASURE_TOKENS", "1")
        with patch("review.prompt.count_tokens", return_value=10) as counter:
            review.registry.build_prompt(Phase.SCOUT, self._job(tmp_path), max_turns=10)
        model = counter.call_args.args[1]
        assert model == self._stats(tmp_path)["token_model"]


class TestPromptStatsConcurrentAppends:
    """Concurrent group agents must not drop each other's prompt-stats records."""

    def test_every_concurrent_append_is_kept(self, tmp_path):
        job = _make_job()
        job.review_file = str(tmp_path / "review.md")
        n = 32

        def write(i):
            log_prompt_size(
                f"t{i}", f"p{i}", {}, job,
                budget_bytes=10_000, model=TEST_MODEL,
            )

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(write, range(n)))

        stats = json.loads((tmp_path / "prompt-stats.json").read_text())
        assert len(stats) == n
        assert {row["template"] for row in stats} == {f"t{i}" for i in range(n)}

    def test_a_write_failure_is_logged_with_the_exception_detail(self, tmp_path, monkeypatch, capsys):
        job = _make_job()
        job.review_file = str(tmp_path / "review.md")

        def _boom(path, data):
            raise OSError("disk full")

        monkeypatch.setattr("review.prompt.write_json", _boom)

        log_prompt_size(
            "t", "p", {}, job,
            budget_bytes=10_000, model=TEST_MODEL,
        )

        stderr = capsys.readouterr().err
        assert "disk full" in stderr
