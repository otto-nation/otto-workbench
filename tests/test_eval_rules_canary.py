"""Tests for the --add-dir rules-loading canary.

The live invocation is not exercised here — `_no_live_backend` forbids spawning
a real CLI, and the two-call measurement is what the Eval workflow runs. What is
covered is everything between the CLI's reply and the verdict: the envelope
parsing, the floor comparison, and the unmeasured-run cases that must not read
as a regression.

The envelope fixtures are real output from Claude Code 2.1.265, trimmed to the
fields the parser reads.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from eval.rules_canary import (  # noqa: E402
    RULES_PREFIX_FLOOR,
    CanaryResult,
    CanaryRun,
    billed_input_from_envelope,
    fixture_text,
    write_fixture,
)

# The reference measurement from Claude Code 2.1.265 on the first-party API,
# reproduced from the comment block above `RULES_PREFIX_FLOOR` in
# eval/rules_canary.py — named here so the figures below share one source
# instead of drifting apart as separate literals.
REFERENCE_WITH_ADD_DIR = 42435
REFERENCE_WITHOUT_ADD_DIR = 2376
REFERENCE_DELTA = REFERENCE_WITH_ADD_DIR - REFERENCE_WITHOUT_ADD_DIR
REFERENCE_EMPTY_CWD = 33646  # flag held on, empty cwd — the flag-ignored stand-in


def _envelope(*, input_tokens=2, cache_read=0, cache_write=0) -> str:
    """A `--output-format json` reply carrying the given usage."""
    return json.dumps({
        "type": "result",
        "subtype": "success",
        "result": "OK",
        "usage": {
            "input_tokens": input_tokens,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_write,
            "output_tokens": 4,
        },
    })


def _run(billed: int, exit_code: int = 0) -> CanaryRun:
    return CanaryRun(billed_input=billed, exit_code=exit_code)


class TestBilledInputFromEnvelope:
    def test_sums_the_three_billed_fields(self):
        stdout = _envelope(input_tokens=2, cache_read=1367, cache_write=40882)
        assert billed_input_from_envelope(stdout) == 42251

    def test_cold_and_warm_runs_of_one_prefix_agree(self):
        """The same prefix bills as writes when cold and reads when warm.

        This is why the canary measures billed input rather than cache creation:
        keyed on writes, the warm run below reads as a zero-token prefix and the
        check would fail on every run after the first.
        """
        cold = billed_input_from_envelope(_envelope(input_tokens=2, cache_write=40882, cache_read=1367))
        warm = billed_input_from_envelope(_envelope(input_tokens=2, cache_write=0, cache_read=42249))
        assert cold == warm == 42251

    def test_reads_the_stream_json_array_shape(self):
        """`--verbose` replies are an array of records, not one object."""
        stdout = json.dumps([
            {"type": "system", "subtype": "init"},
            {"type": "assistant"},
            json.loads(_envelope(input_tokens=2, cache_write=42433)),
        ])
        assert billed_input_from_envelope(stdout) == 42435

    def test_unparseable_output_is_unmeasured_not_an_exception(self):
        assert billed_input_from_envelope("Claude configuration file not found") == 0

    def test_envelope_without_usage_is_unmeasured(self):
        assert billed_input_from_envelope(json.dumps({"type": "result", "result": "OK"})) == 0


class TestCanaryRunMeasured:
    def test_a_billed_run_is_measured(self):
        assert _run(42435).measured

    def test_zero_tokens_on_a_clean_exit_is_not_measured(self):
        """An untrusted workspace and a missing config both exit 0 billing nothing."""
        assert not _run(0).measured

    def test_a_failed_run_is_not_measured(self):
        assert not _run(42435, exit_code=1).measured


class TestCanaryVerdict:
    def test_a_delta_above_the_floor_passes(self):
        result = CanaryResult(_run(REFERENCE_WITH_ADD_DIR), _run(REFERENCE_WITHOUT_ADD_DIR))
        assert result.delta == REFERENCE_DELTA
        assert result.ok
        assert "still loads" in result.summary

    def test_the_flag_being_ignored_fails(self):
        """The failure this exists to catch: both halves bill the same prefix."""
        result = CanaryResult(_run(REFERENCE_EMPTY_CWD), _run(REFERENCE_EMPTY_CWD))
        assert result.delta == 0
        assert not result.ok
        assert "running without coding rules" in result.summary

    def test_a_delta_below_the_floor_fails(self):
        result = CanaryResult(_run(10000), _run(9000))
        assert not result.ok

    def test_an_inverted_delta_is_not_a_pass(self):
        """The run *without* the flag billing more is not the coupling working.

        The delta is directional on purpose. Compared as a magnitude, this case
        clears any floor — so a check that lost the sign would report the rules
        arriving on a run that demonstrates the opposite.
        """
        result = CanaryResult(_run(2376), _run(42435))
        assert result.delta == -REFERENCE_DELTA
        assert not result.ok

    def test_exactly_the_floor_passes(self):
        result = CanaryResult(_run(10000 + RULES_PREFIX_FLOOR), _run(10000))
        assert result.delta == RULES_PREFIX_FLOOR
        assert result.ok

    def test_an_unmeasured_half_is_not_reported_as_a_regression(self):
        """Two zero-token runs have a zero delta, which looks exactly like the
        regression. Saying so would send someone to debug a rules pipeline that
        was never invoked."""
        result = CanaryResult(_run(0), _run(0))
        assert result.delta == 0
        assert not result.ok
        assert not result.measured
        assert "did not measure" in result.summary
        assert "billing no tokens" in result.summary
        assert "running without coding rules" not in result.summary

    def test_a_cli_that_never_ran_says_so(self):
        """127 is `claude` not being installed, which is not a credentials
        problem and should not read as one."""
        result = CanaryResult(_run(0, exit_code=127), _run(0, exit_code=127))
        assert not result.measured
        assert "exited 127 without producing a reply" in result.summary
        assert "billing no tokens" not in result.summary

    def test_a_failed_half_fails_even_with_a_passing_delta(self):
        """A crashed run can still bill for its prefix before dying.

        The delta then clears the floor on arithmetic alone, so a verdict that
        checked only the floor would report the rules pipeline healthy on the
        strength of a run that never completed.
        """
        result = CanaryResult(_run(REFERENCE_WITH_ADD_DIR, exit_code=1), _run(REFERENCE_WITHOUT_ADD_DIR))
        assert result.delta >= result.floor
        assert not result.measured
        assert not result.ok
        assert "did not measure" in result.summary

    def test_a_custom_floor_is_honoured(self):
        assert CanaryResult(_run(15000), _run(10000), floor=9000).ok is False
        assert CanaryResult(_run(15000), _run(10000), floor=4000).ok is True


class TestFixture:
    def test_the_fixture_is_planted_where_claude_reads_it(self, tmp_path):
        root = write_fixture(tmp_path / "fixture")
        assert (root / "CLAUDE.md").read_text(encoding="utf-8") == fixture_text()

    def test_the_fixture_is_large_enough_to_measure(self):
        """A fixture worth fewer tokens than the floor could never clear it."""
        assert len(fixture_text()) > RULES_PREFIX_FLOOR * 4
