"""Does `--add-dir` still bring the operator's coding rules with it.

`agent.backend_claude._base_cmd` runs `claude -p --bare`, which skips CLAUDE.md
auto-discovery. What puts the rules back is `--add-dir`, a flag passed for
filesystem reasons: it restores memory loading wholesale, not merely access to
the directory it names. That coupling is undocumented Claude behaviour, so a CLI
upgrade could keep honouring the flag while dropping what it implies — every
agent would then run with no coding rules, with no error and no missing file.
The call sites are already pinned by test; what is not pinned is the behaviour
those call sites depend on, which is what this measures.

## Why the measurement is a difference of two runs

The obvious check — run once with the flag and assert the prefix is large — does
not work, and the way it fails is the reason this module exists. A run in an
empty directory still bills ~33.5k tokens with the flag set: the CLI's own
system prompt, its tool definitions, and the operator's `~/.claude` memory. Only
the remainder is the directory's rules. An absolute floor anywhere below that
baseline passes on a run where the project rules never loaded at all, which is
precisely the failure being watched for.

So both halves run against the same planted fixture and differ in nothing but
the flag. Everything not attributable to the flag — system prompt, tools, user
memory — appears in both halves and cancels. Sanitising `HOME` to remove the
user memory instead is not an option: the CLI refuses to start without its
config file, and the refusal bills zero tokens while exiting 0.

## Why the fixture is planted rather than this repo

Measuring against the workbench's own rules would tie the floor to how large
that corpus happens to be in the week the check runs, and ordinary rule edits
would move it. The canary writes its own `CLAUDE.md` of known size, so the
expected delta is derived from what it planted rather than from a number
observed once on one machine.

`billed_input` is the metric, not `cache_creation_input_tokens`. A cold run
bills the prefix as cache writes and a warm one as cache reads — the same
prefix, moved between two fields. A check keyed on writes reads a warm run as a
zero delta, so it would pass on the first CI run of the day and fail on the
second for no reason anyone could act on.
"""

# doc-group: eval

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from agent.usage import usage_from_records

# One line of plausible rule prose, repeated to build a fixture of known size.
# The content is irrelevant — nothing reads it back — but the shape is not: a
# file of repeated identical bytes is not what a rule corpus tokenises like.
_FIXTURE_LINE = "- Canary rule line: a sentence of plausible coding-rule prose, one of many.\n"
_FIXTURE_LINES = 300

# Measured against Claude Code 2.1.265 on the first-party API, all figures
# reproducible to the token across repeated runs:
#
#   flag on vs flag off, planted cwd    42,435 vs 2,376   delta 40,059
#   planted vs empty cwd, flag held on  42,435 vs 33,646  delta  8,789
#
# The first is what this check measures. The second is what the planted fixture
# is worth on its own, and it is the number the floor is set against — because
# most of that 40,059 is the operator's own `~/.claude` memory, which a CI
# runner does not have. A floor derived from the larger figure would pass on a
# developer's machine and fail in the environment the check actually runs in.
#
# Well under 8,789 even so. The number is not a constant of nature —
# tokenisation, the system prompt and the memory layout all move with a CLI
# release — and this check exists to catch the flag being ignored, not to
# ratchet prefix size. The failure it is aimed at takes the delta to roughly
# zero, so anything that separates "the rules arrived" from "they did not" is
# enough, and the remaining distance is headroom against an ordinary upgrade.
RULES_PREFIX_FLOOR = 4000

# The prompt is deliberately trivial: the reply is not read, and a prompt the
# model has to think about would put output tokens in the way of the one number
# this measures.
CANARY_PROMPT = "Reply with only the word OK."


def fixture_text() -> str:
    """The CLAUDE.md the canary plants in its working directory."""
    return "# Canary rules\n\n" + _FIXTURE_LINE * _FIXTURE_LINES


def write_fixture(root: Path) -> Path:
    """Plant the fixture rule file under *root* and return the directory."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "CLAUDE.md").write_text(fixture_text(), encoding="utf-8")
    return root


@dataclass(frozen=True)
class CanaryRun:
    """One measured invocation. `billed_input` is 0 when nothing was measured."""

    billed_input: int
    exit_code: int

    @property
    def measured(self) -> bool:
        """Whether this run produced a number that means anything.

        A zero here is not a cheap run, it is an absent measurement — and the
        awkward ones arrive on a clean exit. An untrusted workspace and a
        missing config file each print a notice, bill nothing and exit 0, so the
        exit code alone does not separate them from a real reading. Treating
        either as one would report a zero delta, which is the exact shape of the
        failure this check watches for, so both conditions are required.
        """
        return self.exit_code == 0 and self.billed_input > 0


@dataclass(frozen=True)
class CanaryResult:
    """Both halves of the comparison and what they say together."""

    with_add_dir: CanaryRun
    without_add_dir: CanaryRun
    floor: int = RULES_PREFIX_FLOOR

    @property
    def delta(self) -> int:
        return self.with_add_dir.billed_input - self.without_add_dir.billed_input

    @property
    def measured(self) -> bool:
        return self.with_add_dir.measured and self.without_add_dir.measured

    @property
    def ok(self) -> bool:
        """True only when both halves measured and the delta clears the floor."""
        return self.measured and self.delta >= self.floor

    @property
    def _unmeasured_reason(self) -> str:
        """Which way the canary failed to measure.

        A run that never started and one that answered without billing anything
        are both unmeasured, but they send a reader to different places: the
        first to the runner's toolchain, the second to the CLI's trust and
        config state. Reporting them with one sentence sent people to check
        credentials for a `claude` that was not installed.
        """
        failed = [r for r in (self.with_add_dir, self.without_add_dir) if r.exit_code != 0]
        if failed:
            return f"a run exited {failed[0].exit_code} without producing a reply"
        return "a run exited 0 while billing no tokens"

    @property
    def summary(self) -> str:
        if not self.measured:
            return (
                f"canary did not measure anything — {self._unmeasured_reason}. "
                "A missing CLI, missing credentials, an untrusted workspace or a "
                "missing config all look like this. It is a broken canary, not a "
                "rules regression."
            )
        if not self.ok:
            return (
                f"--add-dir no longer loads the rule file: delta {self.delta} tokens, "
                f"floor {self.floor}. Every agent is running without coding rules."
            )
        return f"--add-dir still loads the rule file: delta {self.delta} tokens (floor {self.floor})."


def billed_input_from_envelope(stdout: str) -> int:
    """Pull `billed_input` out of a `--output-format json` reply.

    The arithmetic belongs to `agent.usage`, which already knows both CLI
    spellings of every token field and what counts as billed input; this only
    finds the records and hands them over. A second local definition of "billed
    input" is how the ledger and the canary would come to disagree about what
    the same call cost.

    Unparseable output is 0 rather than an exception: the caller already has to
    treat 0 as unmeasured, and a CLI that changed its envelope shape is the same
    finding as one that billed nothing — neither produced a reading.
    """
    try:
        envelope = json.loads(stdout)
    except (json.JSONDecodeError, ValueError):
        return 0
    records = envelope if isinstance(envelope, list) else [envelope]
    return usage_from_records([r for r in records if isinstance(r, dict)]).billed_input
