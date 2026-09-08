"""Text a human reads, formatted the same way wherever it is written.

Stdlib only, and no domain vocabulary: what lives here is the formatting a
count or a phrase needs before it reaches a log line, a PR comment or a review
document, so a module that only wants to say "3 findings" does not have to
import the review layer to say it.
"""

# doc-group: platform

from __future__ import annotations

import re


def plural(n: int) -> str:
    """Return the plural suffix for a count — `f"{total} finding{plural(total)}"`."""
    return "" if n == 1 else "s"


def slugify(text: str, sep: str = "-") -> str:
    """Lowercase `text` with every run of non-alphanumerics collapsed to `sep`.

    Callers key persistent state on the result, so what matters as much as the
    shape is that it is the same in every process: an id derived from `hash()`
    is not, and a failure identified that way reads as new on the next run.
    """
    return re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep)
