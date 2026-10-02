"""In-phase recovery from an API `prompt is too long` rejection.

The local budget is a prior. When the API still rejects the rendered prompt,
the rejection names how many tokens were sent and how many the model will
take. That pair is enough to rebuild at a density just measured, without
waiting for `--recover` — which would re-render the same bytes.

Unparseable rejections stay non-recoverable. Local `PROMPT_TOO_LARGE` is a
different kind and is never retried here.
"""

# doc-group: pipeline

from __future__ import annotations

import re
from collections.abc import Callable
from typing import TypeVar

import core.log
from agent.diagnosis import Diagnosis, DiagnosisKind
from review.budget import COMPLETION_RESERVE_TOKENS
from review.prompt import PromptTooLarge

_T = TypeVar("_T")

# At most two rebuilds after the first rejection. Three agent runs on the
# same phase is already a lot of spend for a prompt that would not fit.
MAX_OVERFLOW_RECOVERY = 2

# Leave a little of the API's reported maximum unspent so a second rejection
# is not the first one minus a rounding error.
_OVERFLOW_MARGIN = 0.95

# Tolerant of the Claude CLI's wording (`prompt is too long: N tokens > M
# maximum`) and of pi's `errorMessage` carrying the same sentence.
_PROMPT_TOO_LONG = re.compile(
    r"prompt is too long[^0-9]*(\d+)\s*tokens?\s*>\s*(\d+)",
    re.IGNORECASE,
)


def parse_prompt_overflow(detail: str) -> tuple[int, int] | None:
    """`(sent, maximum)` from an API overflow, or None if the text does not parse.

    `sent` is N in `N tokens > M maximum`; `maximum` is M.
    """
    if not detail:
        return None
    matched = _PROMPT_TOO_LONG.search(detail)
    if matched is None:
        return None
    sent, maximum = int(matched.group(1)), int(matched.group(2))
    if sent <= 0 or maximum <= 0:
        return None
    return sent, maximum


def overflow_ladder_bytes(sent: int, maximum: int, rendered_bytes: int) -> int:
    """A ladder target scaled from the API's reported window.

    `(M - completion reserve) / N` is the fraction of the rendered prompt that
    would have fit; the margin keeps the next render under M rather than on it.
    """
    usable = maximum - COMPLETION_RESERVE_TOKENS
    if usable <= 0 or sent <= 0 or rendered_bytes <= 0:
        return 1
    scaled = int(rendered_bytes * (usable / sent) * _OVERFLOW_MARGIN)
    return max(1, scaled)


def recover_overflow_prompt(
    diagnosis: Diagnosis | None,
    prompt: str,
    rebuild: Callable[[int], str],
    attempt: int,
) -> str | None:
    """A smaller prompt for an API overflow, or None when recovery cannot help.

    `attempt` is how many recoveries have already run (0 on the first
    rejection). Local `PROMPT_TOO_LARGE` is never recovered here — that kind
    already pulled every lever.
    """
    if diagnosis is None or attempt >= MAX_OVERFLOW_RECOVERY:
        return None
    if diagnosis.kind is DiagnosisKind.PROMPT_TOO_LARGE:
        return None
    parsed = parse_prompt_overflow(diagnosis.detail)
    if parsed is None:
        return None
    sent, maximum = parsed
    target = overflow_ladder_bytes(sent, maximum, len(prompt.encode()))
    core.log.warn(
        f"API rejected the prompt as too long ({sent} tokens > {maximum} "
        f"maximum) — re-planning at {target} bytes "
        f"(recovery {attempt + 1}/{MAX_OVERFLOW_RECOVERY})"
    )
    try:
        rebuilt = rebuild(target)
    except PromptTooLarge:
        return None
    # The fit loop accepts anything within the full budget, not within
    # `target`. When the fixed sections already exceed the target the rebuild
    # comes back about the same size, and re-sending it is a wasted agent run.
    if len(rebuilt.encode()) >= len(prompt.encode()):
        core.log.warn("Re-planned prompt is no smaller — not retrying")
        return None
    return rebuilt


def run_with_overflow_recovery(
    prompt: str,
    *,
    invoke: Callable[[str], _T],
    after: Callable[[str], Diagnosis | None],
    has_output: Callable[[], bool],
    rebuild: Callable[[int], str],
) -> tuple[str, Diagnosis | None]:
    """Invoke, and rebuild at most `MAX_OVERFLOW_RECOVERY` times on overflow.

    `after` runs the same-prompt retry (missing output, max turns) and returns
    the diagnosis when the artifact is still missing. Unparseable rejections
    and local `PROMPT_TOO_LARGE` fall through unchanged.
    """
    attempt = 0
    while True:
        invoke(prompt)
        diagnosis = after(prompt)
        if has_output():
            return prompt, diagnosis
        nxt = recover_overflow_prompt(diagnosis, prompt, rebuild, attempt)
        if nxt is None:
            return prompt, diagnosis
        attempt += 1
        prompt = nxt
