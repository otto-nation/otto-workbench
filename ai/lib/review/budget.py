"""Every bound on what a prompt may carry, and the one fit that spends them.

A prompt has a byte ceiling, and four things compete for it: the diff, the
pre-collected file contents, the incremental delta, and the fixed overhead the
template and the PR header cost. This module owns each of those numbers, so a
collector deciding what to gather and a phase deciding what to send read the
same figure rather than two that drifted apart.

The ceiling is derived, not declared. It starts from the resolved model's
context window, subtracts what the reply and the CLI's own system prompt need,
and prices the remainder in bytes at a density floor — so it is a property of
the model a phase actually runs on rather than a constant that matched none of
them. `prompt_budget_bytes` is that derivation. A tier alias that never resolved
takes its tier's floor rather than failing, because an unset
`ANTHROPIC_DEFAULT_*_MODEL` is the ordinary first-party-API setup; only a
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

from dataclasses import dataclass
from pathlib import Path

from agent.phases import ModelAlias, collect_phase_models
from review.grouping import classify_tier, format_profiles_section

# ── The token ceiling, and the bytes it buys ─────────────────────────────────

# What each model can hold, read from `result.modelUsage[*].contextWindow` in
# real session logs rather than from documentation. A model absent from this
# table has no entry to guess at: `prompt_budget_bytes` refuses rather than
# defaulting, because every default is wrong in the expensive direction on the
# model it was not chosen for.
MODEL_CONTEXT_TOKENS = {
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 200_000,
    "claude-sonnet-4-5": 200_000,
    "claude-opus-4-6": 200_000,
    "claude-haiku-4-5": 200_000,
}

# What an unresolved tier alias is worth. `agent.phases.phase_model` returns the
# literal "sonnet" when ANTHROPIC_DEFAULT_SONNET_MODEL is unset, which is the
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

# The system prompt and tool schemas, which `claude -p` assembles inside the CLI
# where nothing here can see them. `agent.token_count` measures the gap against
# session logs at 9.5k–48.8k tokens — ~26k for a full review phase, ~11k for a
# lighter one — so this covers the top of the observed band with margin. It is a
# reserve because the text is unreachable, not because it is unmeasurable: pass
# `system` and `tools` to `count_tokens` and it becomes a measurement.
OVERHEAD_RESERVE_TOKENS = 64_000

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
# ceiling: a reserve, because the markup is generated during the render the
# budget precedes. Upgrade to a measurement if a render is ever recorded with
# `unaccounted_bytes` above this figure, which is the same record that would
# show the reserve had stopped covering what it is for.
RENDER_MARKUP_RESERVE_BYTES = 16_000

MIN_DIFF_BYTES = 20_000

FILE_CONTENT_DENSITY_THRESHOLD = 0.15
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


class UnknownModelWindow(RuntimeError):
    """A model that is neither on record nor a tier alias to fall back on.

    Raised rather than defaulted, because a model nobody has measured is as
    likely to be narrower than the alias floor as wider, and guessing wide is
    the expensive direction. An unresolved alias is not this case — see
    `ALIAS_FLOOR_TOKENS`.
    """

    def __init__(self, model: str, detail: str = ""):
        self.model = model
        if not detail:
            known = ", ".join(sorted(MODEL_CONTEXT_TOKENS))
            detail = (
                f"no context window on record for model {model!r} — add it to "
                f"review.budget.MODEL_CONTEXT_TOKENS. Known models: {known}"
            )
        super().__init__(detail)

    @classmethod
    def too_narrow(cls, model: str, window: int) -> "UnknownModelWindow":
        """A window the reserves alone exhaust, so no prompt could ever fit.

        Unreachable while every recorded window is 200,000 against 96,000 of
        reserves. It is raised rather than clamped because the alternative is a
        negative budget that `_fit_budget`'s `max(0, ...)` guards absorb
        without complaint — every phase would then refuse every prompt, and the
        reason would be a table entry nobody would think to look at.
        """
        reserved = COMPLETION_RESERVE_TOKENS + OVERHEAD_RESERVE_TOKENS
        return cls(model, (
            f"{model!r} has a {window:,}-token window, which its reserves "
            f"({reserved:,}) exhaust — no prompt could fit. Lower "
            f"COMPLETION_RESERVE_TOKENS or OVERHEAD_RESERVE_TOKENS, or do not "
            f"review with this model."
        ))


def model_window_tokens(model: str) -> int:
    """The context window ``model`` is budgeted against.

    A concrete id is looked up; an unresolved tier alias takes the conservative
    floor its tier guarantees. Anything else raises `UnknownModelWindow`.
    """
    window = MODEL_CONTEXT_TOKENS.get(model)
    if window is not None:
        return window
    if ModelAlias.parse(model) is not None:
        return ALIAS_FLOOR_TOKENS
    raise UnknownModelWindow(model)


def prompt_budget_tokens(model: str) -> int:
    """What one prompt to ``model`` may cost, in tokens.

    The window less what the reply needs and less the system prompt and tool
    schemas the CLI adds out of sight.
    """
    window = model_window_tokens(model)
    budget = window - COMPLETION_RESERVE_TOKENS - OVERHEAD_RESERVE_TOKENS
    if budget <= 0:
        raise UnknownModelWindow.too_narrow(model, window)
    return budget


def prompt_budget_bytes(model: str) -> int:
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
    capability = int(prompt_budget_tokens(model) * BYTES_PER_TOKEN_FLOOR)
    return min(capability, MAX_SPEND_BYTES) - RENDER_MARKUP_RESERVE_BYTES


def collection_budget_bytes(
    explicit_model: str | None = None,
    project_root: Path | str | None = None,
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
        prompt_budget_bytes(model)
        for model in collect_phase_models(explicit_model, project_root)
    )


def fixed_preflight_bytes(
    commit_log: str,
    claude_md: str,
    architecture_md: str,
    review_checklists: dict[str, str],
    review_profiles: list | None = None,
) -> int:
    """The bytes of preflight data no budget lever can shrink.

    `commit_log` is the log the collector gathered, `claude_md` and
    `architecture_md` are the project context files, `review_checklists` is
    every checklist keyed by name, and `review_profiles` is every profile the
    repo declares — the five sections that go into a prompt whole or not at
    all. The diff, the pre-collected file contents and the incremental delta
    are all levers a fit can pull, so none of them is here.

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
        len(commit_log.encode())
        + len(claude_md.encode())
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
