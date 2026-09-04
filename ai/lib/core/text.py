"""Text a human reads, formatted the same way wherever it is written.

Stdlib only, and no domain vocabulary: what lives here is the formatting a
count or a phrase needs before it reaches a log line, a PR comment or a review
document, so a module that only wants to say "3 findings" does not have to
import the review layer to say it.
"""

# doc-group: platform

from __future__ import annotations


def plural(n: int) -> str:
    """Return the plural suffix for a count — `f"{total} finding{plural(total)}"`."""
    return "" if n == 1 else "s"
