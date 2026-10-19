"""Rendering a review record — `review.md` today, the GitHub comment from PR 2."""

# doc-group: findings

from __future__ import annotations

# The line every rendered review.md carries. Ingest refuses any text holding it,
# which is what "review.md is never parsed back" is enforced by.
BANNER = "<!-- rendered from review.json — edits here are overwritten; change the record -->"

# Quoted in the Agent Failures section when a failed phase can be retried.
RECOVER_HINT = "Run `pr review --recover`"
