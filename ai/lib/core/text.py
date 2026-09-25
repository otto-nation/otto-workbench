"""Text a human reads, formatted the same way wherever it is written.

Stdlib only, and no domain vocabulary: what lives here is the formatting a
count or a phrase needs before it reaches a log line, a PR comment or a review
document, so a module that only wants to say "3 findings" does not have to
import the review layer to say it.
"""

# doc-group: platform

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone


def plural(n: int) -> str:
    """Return the plural suffix for a count — `f"{total} finding{plural(total)}"`."""
    return "" if n == 1 else "s"


def age_of(iso_str: str) -> timedelta | None:
    """How long ago `iso_str` was, or `None` when nothing can be read from it.

    `None` rather than a zero delta, because a caller deciding whether a
    snapshot is too old to trust must not read an unreadable stamp as fresh —
    which is the direction that hides a week-old answer rather than an hour-old
    one. An empty stamp, a stamp in a format this does not parse, and a state
    file written before the field existed are all the same unknown.
    """
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
    except (AttributeError, ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - dt


def relative_time(iso_str: str) -> str:
    """How long ago `iso_str` was, as a human reads it — `"3 days ago"`.

    An unparseable or empty stamp is the empty string rather than an error: a
    caller is rendering a line either way, and a state file written by an older
    version is a missing age, not a failure to print the line it sat on.
    """
    delta = age_of(iso_str)
    if delta is None:
        return ""
    hours = delta // timedelta(hours=1)
    if hours < 1:
        return f"{delta // timedelta(minutes=1)} minutes ago"
    days = delta // timedelta(days=1)
    if not days:
        return f"{hours} hour{plural(hours)} ago"
    return f"{days} day{plural(days)} ago"


def join_or(items: list[str]) -> str:
    """Join `items` into an English alternative — `"a dash, a colon, or italics"`.

    Correct at every length, which the slice-and-concatenate form callers reach
    for first is not: `', '.join(items[:-1])` is empty at one item and emits a
    stray leading comma before the last, and raises `IndexError` at none. Both
    lengths are unreachable in a caller joining a constant map today and become
    reachable the moment the map is trimmed, silently in the first case.

    Two items take no comma, as the serial comma separates three or more.
    """
    if not items:
        return ""
    if len(items) == 1:
        return items[0]
    if len(items) == 2:
        return f"{items[0]} or {items[1]}"
    return f"{', '.join(items[:-1])}, or {items[-1]}"


def summarize_comment_body(body: str, max_len: int = 120) -> str:
    """Extract first meaningful line from a comment body, truncated.

    Here rather than beside either reader because two of them now exist: the
    summary table's raw-comment sections name a top-level comment this way, and
    `pr.settlement` names a thread no triage round ever wrote a summary for. A
    second copy would let the two tables call one comment different things.
    """
    in_html_comment = False
    for raw_line in body.splitlines():
        stripped = raw_line.strip()
        if in_html_comment:
            in_html_comment = "-->" not in stripped
            continue
        if stripped.startswith("<!--"):
            in_html_comment = "-->" not in stripped
            continue
        line = stripped.lstrip("#").strip()
        if not line:
            continue
        if len(line) > max_len:
            return line[:max_len - 1] + "…"
        return line
    return "(empty)"


def slugify(text: str, sep: str = "-") -> str:
    """Lowercase `text` with every run of non-alphanumerics collapsed to `sep`.

    Callers key persistent state on the result, so what matters as much as the
    shape is that it is the same in every process: an id derived from `hash()`
    is not, and a failure identified that way reads as new on the next run.
    """
    return re.sub(r"[^a-z0-9]+", sep, text.lower()).strip(sep)
