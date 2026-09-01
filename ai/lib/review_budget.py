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
