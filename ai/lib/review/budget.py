"""Every bound on what a prompt may carry, and the one fit that spends them.

A prompt has a byte ceiling, and four things compete for it: the diff, the
pre-collected file contents, the incremental delta, and the fixed overhead the
template and the PR header cost. This module owns each of those numbers, so a
collector deciding what to gather and a phase deciding what to send read the
same figure rather than two that drifted apart.

`agent_types.RetryBudget` is a different thing that shares the word — it
budgets retries, not bytes.
"""

# doc-group: pipeline

from __future__ import annotations

from dataclasses import dataclass

from review.grouping import classify_tier

# ── Byte budgets ──────────────────────────────────────────────────────────────

MAX_PROMPT_TOKENS = 120_000
MAX_PROMPT_BYTES = MAX_PROMPT_TOKENS * 4
TEMPLATE_OVERHEAD_BYTES = 20_000
MAX_FILE_BYTES = 100_000
MAX_TRUNCATED_LINES = 500
MAX_COMMIT_LOG_BYTES = 50_000
MAX_DELTA_DIFF_BYTES = 80_000
MAX_DELTA_LOG_BYTES = 20_000

# ceiling: a flat reserve for everything in a prompt that is not preflight data —
# the template, the PR header, prior reviews, reply threads. `review_prompt` now
# measures those sections exactly before it budgets, so this double-counts them:
# on a typical prompt it holds back ~116KB nothing spends, and the review is
# smaller than it had room to be. Shrinking it is not free — every byte returned
# is a byte of diff sent to the model, so it raises per-review cost, which is why
# it is left as-is while review cost is what is being worked on. Upgrade when a
# phase reports a cut in its prompt stats that this reserve alone would have
# covered, or once per-review cost has a budget of its own to spend it against.
NON_PREFLIGHT_OVERHEAD_BYTES = 120_000
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
# budget. `review_collect` bounds the count itself now, by narrowing the delta to
# the review's surface; this bounds the rendering, so no future way of
# over-counting can spend the whole budget on it.
MAX_DELTA_LIST_ENTRIES = 200

# Below this the diff fence holds a fragment of one hunk, which reads as
# corruption rather than as context. The delta section drops its diff entirely
# at that point and the full diff — which covers the same files from the base —
# is what the agent reviews from.
MIN_DELTA_DIFF_BYTES = 2_048


def fixed_preflight_bytes(
    commit_log: str,
    claude_md: str,
    architecture_md: str,
    review_checklists: dict[str, str],
) -> int:
    """The bytes of preflight data no budget lever can shrink.

    `commit_log` is the log the collector gathered, `claude_md` and
    `architecture_md` are the project context files, and `review_checklists`
    is every checklist keyed by name — the four sections that go into a prompt
    whole or not at all. The diff, the pre-collected file contents and the
    incremental delta are all levers a fit can pull, so none of them is here.

    Taken as four values rather than as a `PreflightData`: the collector holds
    them as locals before it has a `PreflightData` to put them in, and this
    module knowing that type would invert the dependency. A caller that has one
    reads the four fields off it, and one that does not spends nothing.
    """
    return (
        len(commit_log.encode())
        + len(claude_md.encode())
        + len(architecture_md.encode())
        + sum(len(v.encode()) for v in review_checklists.values())
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
