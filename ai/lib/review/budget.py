"""Every bound on what a prompt may carry, and the one fit that spends them.

A prompt has a byte ceiling, and four things compete for it: the diff, the
pre-collected file contents, the incremental delta, and the fixed overhead the
template and the PR header cost. This module owns each of those numbers, so a
collector deciding what to gather and a phase deciding what to send read the
same figure rather than two that drifted apart.

The ceiling is derived, not declared. It starts from the resolved model's
context window, read from pi's provider catalogue with `MODEL_CONTEXT_TOKENS` as
the fallback when the catalogue cannot be read or lacks the model. It subtracts
what the reply and the CLI's own system prompt need, and prices the remainder in
bytes at a density floor — so it is a property of
the model a phase actually runs on rather than a constant that matched none of
them. `prompt_budget_bytes` is that derivation. A tier alias that never resolved
takes its tier's floor rather than failing, because an unset
`AI_*_MODEL` is the ordinary first-party-API setup; only a
concrete model nobody has measured raises `UnknownModelWindow`.

The byte figure can only ever be conservative: no byte count bounds a token
count without knowing the content's density, so `BYTES_PER_TOKEN_FLOOR` assumes
content denser than anything measured and the budget spends less than the
window allows. That is the intended direction — too generous is an API
rejection, too conservative is a shallower review.

`agent.types.RetryBudget` is a different thing that shares the word — it
budgets retries, not bytes.
"""

# doc-group: pipeline

from __future__ import annotations

import functools
from dataclasses import dataclass
from pathlib import Path

import core.proc
import core.timeouts
from agent.phases import ModelAlias, collect_phase_models
from core.phases import Backend
from review.grouping import classify_tier, format_profiles_section

# ── The token ceiling, and the bytes it buys ─────────────────────────────────

# Fallback when pi's provider catalogue cannot be read, or does not list the
# model. Figures originally come from `pi --list-models` (and from
# `result.modelUsage[*].contextWindow` in session logs). A model absent from
# both the catalogue and this table has no entry to guess at:
# `prompt_budget_bytes` refuses rather than defaulting, because every default
# is wrong in the expensive direction on the model it was not chosen for.
MODEL_CONTEXT_TOKENS = {
    "claude-sonnet-5": 1_000_000,
    # Absent until a review that resolved to it aborted on UnknownModelWindow.
    # 1M is what the provider catalogue reports (`pi --list-models`), which is
    # the same place a session log's contextWindow comes from.
    "claude-opus-5": 1_000_000,
    # Both 1M per the provider catalogue, the same source as the entries above.
    "claude-sonnet-5-5": 1_000_000,
    "claude-opus-5-5": 1_000_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
}

# What an unresolved tier alias is worth. `agent.phases.phase_model` returns the
# literal "sonnet" when AI_SONNET_MODEL is unset, which is the
# ordinary first-party-API setup rather than a misconfiguration — so this is a
# case to budget conservatively for, not one to refuse. The figure is the
# narrowest window any model in that tier has: assuming the smallest is safe
# whichever concrete model the alias turns out to name, where assuming the
# largest would budget a 200k model against a 1M window.
ALIAS_FLOOR_TOKENS = 200_000

# The reply is not free and comes out of the same window. Sized over the
# largest review output observed rather than the typical one, since the cost of
# being wrong is a truncated finding list.
COMPLETION_RESERVE_TOKENS = 32_000

# The system prompt and tool schemas, which the CLI assembles where nothing
# here can see them. First-request overhead (session input minus counted
# prompt tokens) measured 43–51k on the Claude backend (n=4) and 8–26k on
# pi (n=31). Each backend's reserve covers its observed max with margin.
# An unknown backend takes the larger figure, because guessing small is the
# expensive direction. Pass `system` and `tools` to `count_tokens` and the
# reserve becomes a measurement.
OVERHEAD_RESERVE_TOKENS = 64_000
PI_OVERHEAD_RESERVE_TOKENS = 32_000

# ceiling: a density floor, not an estimate. No byte count can bound a token
# count — the same 480KB is 242k tokens of review prose and 457k of base64 — so
# this converts a token budget into bytes by assuming content denser than
# anything measured. Against 112 exactly-counted prompts on claude-sonnet-5 the
# observed range is 2.23–2.89 B/tok; 2.0 sits below the floor of that range.
# The corpus is one repo's Python and Markdown, so 2.23 is otto-workbench's
# floor and not a universal one, and hash- or base64-dense content goes lower
# still. Being too conservative costs a shallower review; being too generous
# costs an API rejection. Upgrade to an exact count when `count_tokens` can see
# the system prompt and tool schemas, or if a repo reports a rejection under
# this floor.
BYTES_PER_TOKEN_FLOOR = 2.0

# What a review is willing to spend on one prompt, as against what the model
# could physically hold. The two are different bounds and only one of them is
# about correctness: a 1M-token window permits a 1.8MB prompt, which is no
# cheaper to send for being permitted. This holds spend where it has been — it
# is the byte ceiling the budget carried when it was stated as tokens — so
# deriving the window stops an oversized prompt without silently buying a
# bigger one. Against 1,897 recorded renders the median prompt is 50KB and the
# p99 is 235KB, so this binds nothing that has actually run.
MAX_SPEND_BYTES = 480_000

TEMPLATE_OVERHEAD_BYTES = 20_000
MAX_FILE_BYTES = 100_000
MAX_TRUNCATED_LINES = 500
MAX_COMMIT_LOG_BYTES = 50_000
MAX_DELTA_DIFF_BYTES = 80_000
MAX_DELTA_LOG_BYTES = 20_000

# The template's own text and the block markup wrapping each section: bytes
# that reach the prompt without passing a lever, so the ladder cannot see them
# and would otherwise plan right up to the ceiling and render past it. This
# replaces a flat 120KB reserve that covered the same overshoot by also
# double-counting every section `review.prompt` measures exactly. Sized from
# `unaccounted_bytes` across 86 recorded renders — 2.3KB median, 9.6KB worst —
# with room above the worst case.
#
# Held back by `ladder_target_bytes` on the *first* render only, which is
# what the ladder plans against, rather than by `prompt_budget_bytes`, which
# is where a prompt is refused. The two have to differ: the ladder fills its
# sections to whatever target it is given, so a target equal to the ceiling
# is overshot by exactly this markup.
#
# The plan-render-verify loop then measures the real overshoot and ratchets
# the ladder down by it, so this figure is a prior for render one, not a
# bound the later renders still hide behind.
#
# ceiling: a first-render prior, because the markup is generated during the
# render the budget precedes. The loop now measures the overshoot; keep this
# figure only as the opening guess. Upgrade it if a first render is recorded
# with `unaccounted_bytes` above this figure, which is the same record that
# would show the prior had stopped covering what it is for.
RENDER_MARKUP_RESERVE_BYTES = 16_000

MIN_DIFF_BYTES = 20_000

# How little of a file a change may touch before its contents are the first
# thing a collection over budget gives up. Not a reason to withhold the file
# from a collection that fits: below this ratio the diff is a smaller share of
# the file, not a sufficient substitute for it — three lines of hunk context do
# not say whether a change holds the invariant twenty lines above it. Withholding
# a file that would have fit saves nothing either way, since the agent reads it
# back in its own turns and the same bytes reach the same context window.
#
# ceiling: density is (additions + deletions) / post-image lines, so a pure
# deletion reads denser than it is, which errs toward keeping the file — the
# safe direction now that this only orders a shortfall. A pure rename (no
# content edits) reports additions=0, deletions=0 rather than an absent count,
# so it computes as maximally sparse rather than wholly changed; that is the
# correct call here, not a case this errs on, since a rename with nothing
# changed inside it costs a review nothing to shed first. Upgrade to per-hunk
# coverage if hunk ranges are ever computed at collection time.
FILE_CONTENT_DENSITY_THRESHOLD = 0.15

# Below this a file is never sparse whatever its density: the bytes a shortfall
# would recover are smaller than the turn the agent spends reading it back.
FILE_CONTENT_MIN_SIZE = 5120

# How much of somebody else's prose a prompt quotes back: a prior review's body,
# a review comment, the root of a thread being re-reviewed. Each one is a
# gist — enough for the agent to recognise what was said and go read the thread
# — and there is no bound on how many of them a busy PR contributes, which is
# why the cap is per-body rather than on the section they land in.
MAX_REVIEW_BODY_LEN = 200

# How many paths either file list in the delta section spells out before it
# summarises the rest. A list is orientation, not content — the diff above it is
# what the agent reviews — so the tail costs bytes no reader spends. Both lists
# were uncapped until a rebased branch produced 4,974 delta files for a 107-file
# PR and 260KB of `- \`path\`` lines pushed the synthesis prompt 75% past its
# budget. `review.collect` bounds the count itself now, by narrowing the delta to
# the review's surface; this bounds the rendering, so no future way of
# over-counting can spend the whole budget on it.
MAX_DELTA_LIST_ENTRIES = 200

# Below this the diff fence holds a fragment of one hunk, which reads as
# corruption rather than as context. The delta section drops its diff entirely
# at that point and the full diff — which covers the same files from the base —
# is what the agent reviews from.
MIN_DELTA_DIFF_BYTES = 2_048


def _parse_context_tokens(raw: str) -> int | None:
    """A `pi --list-models` context cell as an integer token count, or None.

    Suffixes: K=1_000, M=1_000_000. Decimals are allowed. The result is
    floored — overestimating a window is the expensive direction.
    """
    text = raw.strip()
    if not text:
        return None
    multiplier = 1
    suffix = text[-1]
    if suffix in ("K", "k"):
        multiplier = 1_000
        text = text[:-1]
    elif suffix in ("M", "m"):
        multiplier = 1_000_000
        text = text[:-1]
    try:
        amount = float(text)
    except ValueError:
        return None
    if amount < 0:
        return None
    tokens = int(amount * multiplier)
    if tokens <= 0:
        return None
    return tokens


def _parse_pi_list_models(text: str) -> dict[str, int]:
    """Model id -> context window, taking the minimum across providers.

    Assumes the model column is a bare id — the same spelling a resolved
    review model uses (`claude-haiku-4-5`), with no `@version` or
    `provider/` decoration. A deployment whose provider lists ids that way
    (`claude-haiku-4-5@20251001`, `xai/grok-4.6`) would never match here and
    would fall through to `MODEL_CONTEXT_TOKENS` silently rather than loudly
    — worth re-checking this assumption if the catalogue's own format ever
    changes.
    """
    windows: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 3:
            continue
        tokens = _parse_context_tokens(parts[2])
        if tokens is None:
            continue
        model = parts[1]
        previous = windows.get(model)
        if previous is None or tokens < previous:
            windows[model] = tokens
    return windows


def _run_pi_list_models() -> str:
    """Stdout of ``pi --list-models``, or empty when the catalogue cannot be read.

    The empty string is a silent fallback, not an error: a review must still
    budget against the recorded table when pi is absent, hung, or speaking a
    format we do not parse. Raising would turn an optional live lookup into a
    hard dependency on a binary the table exists to replace.
    """
    try:
        result = core.proc.run(
            ["pi", "--list-models"], timeout=core.timeouts.LOCAL,
        )
    except OSError:
        # Not just FileNotFoundError: a `pi` present on disk but not
        # executable raises PermissionError, another OSError subclass, and
        # that must fall back the same way — the stated goal is that pi
        # absent, hung, or unreadable is never a hard dependency.
        return ""
    if not result.ok:
        return ""
    return result.stdout


@functools.cache
def _pi_catalogue_windows() -> dict[str, int]:
    """Parsed provider catalogue, populated at most once per process."""
    return _parse_pi_list_models(_run_pi_list_models())


class UnknownModelWindow(RuntimeError):
    """A model that is in neither pi's catalogue nor the fallback table.

    Raised rather than defaulted, because a model nobody has measured is as
    likely to be narrower than the alias floor as wider, and guessing wide is
    the expensive direction. An unresolved alias is not this case — see
    `ALIAS_FLOOR_TOKENS`.
    """

    def __init__(self, model: str, detail: str = ""):
        self.model = model
        if not detail:
            # Both sources, not just the fallback table: when the catalogue is
            # readable it usually knows more models than MODEL_CONTEXT_TOKENS
            # ever will, and a reader chasing a typo'd model name wants the
            # full list this process could actually have resolved against.
            catalogue = _pi_catalogue_windows()
            known = ", ".join(sorted(set(MODEL_CONTEXT_TOKENS) | set(catalogue)))
            # An empty catalogue means pi was absent, failed, or unparsed — not
            # that it lacks the model — so the message must not claim it does.
            where = (
                "pi's provider catalogue or review.budget.MODEL_CONTEXT_TOKENS"
                if catalogue else
                "review.budget.MODEL_CONTEXT_TOKENS (pi's provider catalogue "
                "could not be read)"
            )
            detail = (
                f"no context window on record for model {model!r} — not in "
                f"{where}. Known models: {known}"
            )
        super().__init__(detail)

    @classmethod
    def too_narrow(
        cls, model: str, window: int, backend: Backend | None = None,
    ) -> "UnknownModelWindow":
        """A window the reserves alone exhaust, so no prompt could ever fit.

        Unreachable while every recorded window is 200,000 against 96,000 of
        reserves on Claude (64,000 on pi). It is raised rather than clamped
        because the alternative is a negative budget that `_fit_budget`'s
        `max(0, ...)` guards absorb without complaint — every phase would then
        refuse every prompt, and the reason would be a table entry nobody
        would think to look at.
        """
        reserved = COMPLETION_RESERVE_TOKENS + overhead_reserve_tokens(backend)
        return cls(model, (
            f"{model!r} has a {window:,}-token window, which its reserves "
            f"({reserved:,}) exhaust — no prompt could fit. Lower "
            f"COMPLETION_RESERVE_TOKENS or the backend overhead reserve, or do "
            f"not review with this model."
        ))


def model_window_tokens(model: str) -> int:
    """The context window ``model`` is budgeted against.

    Order: pi's provider catalogue, then `MODEL_CONTEXT_TOKENS`, then the
    unresolved-alias floor. Anything else raises `UnknownModelWindow`.
    """
    window = _pi_catalogue_windows().get(model)
    if window is not None:
        return window
    window = MODEL_CONTEXT_TOKENS.get(model)
    if window is not None:
        return window
    if ModelAlias.parse(model) is not None:
        return ALIAS_FLOOR_TOKENS
    raise UnknownModelWindow(model)


def overhead_reserve_tokens(backend: Backend | None = None) -> int:
    """Tokens reserved for CLI system prompt and tool schemas on ``backend``.

    ``None`` asks the selected backend. An unknown or unselected backend takes
    the larger reserve — guessing small is the expensive direction.
    """
    if backend is None:
        from agent.backend import selected_backend

        backend = selected_backend()
    if backend is Backend.PI:
        return PI_OVERHEAD_RESERVE_TOKENS
    return OVERHEAD_RESERVE_TOKENS


def prompt_budget_tokens(
    model: str, backend: Backend | None = None,
) -> int:
    """What one prompt to ``model`` may cost, in tokens.

    The window less what the reply needs and less the system prompt and tool
    schemas the CLI adds out of sight, priced for ``backend``.
    """
    window = model_window_tokens(model)
    budget = window - COMPLETION_RESERVE_TOKENS - overhead_reserve_tokens(backend)
    if budget <= 0:
        raise UnknownModelWindow.too_narrow(model, window, backend)
    return budget


def prompt_budget_bytes(
    model: str, backend: Backend | None = None,
) -> int:
    """What one prompt to ``model`` may cost, in bytes of rendered prompt.

    The lesser of what the model can hold and what a review will spend. The
    first is the token budget priced at `BYTES_PER_TOKEN_FLOOR`, a bound rather
    than an estimate — see that constant for why a byte ceiling can only be
    conservative. The second is `MAX_SPEND_BYTES`, which keeps a large window
    from quietly becoming a large bill.

    So a wide-window model budgets at today's spend and a narrow one budgets
    below it, which is the case the fused constant got wrong. Raises
    `UnknownModelWindow` for a model with no recorded window.
    """
    capability = int(
        prompt_budget_tokens(model, backend) * BYTES_PER_TOKEN_FLOOR
    )
    return min(capability, MAX_SPEND_BYTES)


def ladder_target_bytes(
    model: str, backend: Backend | None = None,
) -> int:
    """What the budget ladder may plan to spend, below the refusal ceiling.

    `prompt_budget_bytes` is where a prompt is refused; this is what the ladder
    aims at on the first render, and the gap between them is
    `RENDER_MARKUP_RESERVE_BYTES`. They have to be two numbers: the ladder
    plans its sections up to whatever it is given and the render then adds
    markup no lever sized, so a ladder aimed at the ceiling overshoots it by
    exactly the bytes the reserve exists to cover. Later renders ratchet from
    the measured overshoot rather than subtracting this prior again.
    """
    return prompt_budget_bytes(model, backend) - RENDER_MARKUP_RESERVE_BYTES


def collection_budget_bytes(
    explicit_model: str | None = None,
    project_root: Path | str | None = None,
    backend: Backend | None = None,
) -> int:
    """The ceiling collection may gather against, across every review phase.

    Collection runs once and its result is read by every phase, so it has no
    single model to budget against. It takes the smallest phase budget: what
    fits the tightest-windowed phase fits all of them, whereas the largest
    would hand a phase more than its model can hold. Raises
    `UnknownModelWindow` if any phase resolves a model with no recorded window.

    ``project_root`` is the worktree being reviewed, so a per-phase model set
    in its `.workbench.yml` is one of the models this minimum is taken over.
    Without it the phases resolve against the global scope alone and collection
    can be sized against a ceiling no phase actually budgets to.
    """
    return min(
        ladder_target_bytes(model, backend)
        for model in collect_phase_models(explicit_model, project_root)
    )


def fixed_preflight_bytes(
    instructions_md: str,
    architecture_md: str,
    review_checklists: dict[str, str],
    review_profiles: list | None = None,
) -> int:
    """The bytes of preflight data no budget lever can shrink.

    `instructions_md` and `architecture_md` are the project context files,
    `review_checklists` is every checklist keyed by name, and `review_profiles`
    is every profile the repo declares — the sections that go into a prompt
    whole or not at all. The diff, the pre-collected file contents, the
    incremental delta and the commit log are all levers a fit can pull, so
    none of them is here.

    Profiles are measured as `format_profiles_section` will render them, not as
    the sum of their source files: the rendered section carries a heading and a
    preamble no source file holds, and a rule's text is reordered rather than
    copied. Every profile is counted, because a prompt with no file filter
    renders every one of them. A caller that scoped the profiles itself — a
    group prompt matches its own files and registers the result as its own
    section — asks `review.prompt` to skip the project context here rather than
    being reserved for twice.

    Taken as values rather than as a `PreflightData`: the collector holds them
    as locals before it has a `PreflightData` to put them in, and this module
    knowing that type would invert the dependency. A caller that has one reads
    the fields off it, and one that does not spends nothing.
    """
    return (
        len(instructions_md.encode())
        + len(architecture_md.encode())
        + sum(len(v.encode()) for v in review_checklists.values())
        + len(format_profiles_section(review_profiles or []).encode())
    )


# ── File fitting ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class FileFit:
    """Which pre-collected files fit under a ceiling, and which did not.

    `included` and `permissions` are keyed the same way — a path present in
    one is present in the other. `omitted` is every path `fit_files` ranked
    below the ceiling; a caller names those paths to whoever reads the prompt
    rather than letting them go missing silently.
    """

    included: dict[str, str]
    permissions: dict[str, str]
    omitted: list[str]

    @property
    def any_included(self) -> bool:
        """Whether the fit kept at least one file."""
        return bool(self.included)


def fit_files(
    contents: dict[str, str],
    permissions: dict[str, str],
    ceiling: int,
) -> FileFit:
    """The files from `contents` that fit in `ceiling` bytes, cheapest useful first.

    `contents` is every candidate file's text, keyed by path. `permissions` is
    the per-path mode string a caller read for each of them, carried alongside
    so it never has to be re-associated with whatever subset makes the cut.
    `ceiling` is the total bytes the kept files may spend together.

    Ranked by `(classify_tier, size)` — the cheapest useful file first — so a
    ceiling too low for everything still buys the files most worth having.
    Which files are worth shedding first is `_fit_to_budget`'s question, not
    this one's: it sheds sparse files to cover an overflow before calling here,
    so what arrives is already the set worth ranking on tier and size alone.
    """
    sizes = {p: len(c.encode()) for p, c in contents.items()}
    included: dict[str, str] = {}
    included_perms: dict[str, str] = {}
    omitted: list[str] = []
    remaining = max(0, ceiling)
    for path in sorted(contents, key=lambda p: (classify_tier(p), sizes[p])):
        if sizes[path] <= remaining:
            included[path] = contents[path]
            included_perms[path] = permissions.get(path, "")
            remaining -= sizes[path]
        else:
            omitted.append(path)
    return FileFit(included, included_perms, omitted)
