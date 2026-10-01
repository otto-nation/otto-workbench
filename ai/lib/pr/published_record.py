"""What we published, recorded inside what we published.

The summary comment and our thread replies are read back on later rounds to
answer two questions: which row is which, and did a person rewrite what we
wrote. Local state cannot answer either — it is per target and per worktree,
and absent after `pr gc`, after a worktree is recreated, and for a round run on
another machine — so the published text is the only record there is.

Recovering those answers from the rendered prose is what this replaces. Every
row and every generated reply now carries an invisible HTML comment declaring
its identity and a digest of the exact text we wrote. Identity is read off the
marker rather than re-derived from cells a renderer is free to change, and
authorship is a digest comparison: any edit to the text a person can see,
anywhere in it, is a mismatch.

Every value a marker holds is hex, a `FixOutcome` value, or empty, so a marker
can contain neither the `|` that would split its cell nor the `-->` that would
close it early.

What is not here: what a row's identity *is* (`summary_model.row_key_from_cells`)
and the reading of a comment written before markers existed
(`summary_scope.published_rows`, `thread_replies.is_generated_reply`).
"""

# doc-group: publishing

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from pr.fix import FixOutcome

# A body carrying this was written by code that marks every row it writes, so a
# row in it without a marker is a person's. The comment declares its own format;
# no clock and no state schema decides it.
FORMAT_MARKER = "<!-- pr-comments:format 2 -->"

_ROW_MARKER_LEAD = "<!-- pr-comments:row "
# The marker and the one space `mark_row` writes after it. Removing exactly that
# much is what makes `unmarked(mark_row(line))` the line that was marked.
_ROW_MARKER_RE = re.compile(re.escape(_ROW_MARKER_LEAD) + r"([^>]*?) --> ?")
_FIELD_RE = re.compile(r"(\w+)=(\S*)")

_REPLY_MARKER_RE = re.compile(r"<!-- pr-comments:generated sha=([0-9a-f]*) -->")

# Sixteen hex digits is 64 bits — collision-free for any number of rows a PR
# carries, and short enough that a marker per row costs about a hundred bytes
# against GitHub's 65,536-character comment limit.
_DIGEST_HEX = 16


def normalise(text: str) -> str:
    """Text as it compares, whatever round trip it took through GitHub.

    The browser editor saves line endings as CRLF, and a reply edited and saved
    back unchanged must still read as ours. Trailing whitespace goes for the
    same reason: it is invisible, and nobody edits it on purpose.
    """
    lines = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(line.rstrip() for line in lines).strip()


def fingerprint(text: str) -> str:
    """A short digest of `text` as normalised. "" for empty text.

    Empty maps to empty so an absent fold key or row key stays falsy once
    hashed, and never matches another absent one by colliding on a digest.
    """
    text = normalise(text)
    if not text:
        return ""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:_DIGEST_HEX]


@dataclass(frozen=True)
class RowRecord:
    """What a marker declares about the row it sits in.

    ``key`` is the row's identity, hashed — the one key space every reader
    compares in, legacy rows included. ``location`` and ``text_key`` are the
    hashed fold keys, set on comment-item rows only, because only those can be
    folded into a thread row. ``outcome`` is what the Action cell reported when
    we wrote it. ``digest`` is the digest of the row we wrote, and "" for a row
    that was not ours when it was marked.
    """

    key: str
    location: str = ""
    text_key: str = ""
    outcome: FixOutcome | None = None
    digest: str = ""


def unmarked(line: str) -> str:
    """A table row with its marker taken out, normalised for comparison."""
    return normalise(_ROW_MARKER_RE.sub("", line, count=1))


def mark_row(line: str, record: RowRecord, *, ours: bool) -> str:
    """`line` with `record` written into its first cell.

    ``ours`` decides the digest. A row we are writing now is ours, and gets the
    digest of exactly this text. A row a person wrote is marked with an empty
    digest, so it keeps its identity and stays theirs.

    Any marker already on the line is replaced rather than stacked.
    """
    body = unmarked(line)
    digest = fingerprint(body) if ours else ""
    fields = [f"k={record.key}"]
    if record.location:
        fields.append(f"loc={record.location}")
    if record.text_key:
        fields.append(f"txt={record.text_key}")
    if record.outcome is not None:
        fields.append(f"out={record.outcome.value}")
    fields.append(f"d={digest}")
    marker = f"{_ROW_MARKER_LEAD}{' '.join(fields)} -->"
    if body.startswith("| "):
        return f"| {marker} {body[2:]}"
    return f"{marker} {body}"


def read_row_marker(line: str) -> RowRecord | None:
    """The record a row's marker declares, or None for a row with no marker.

    An outcome this code no longer knows reads as none, the same as a cell a
    person wrote: there is no claim left to compare against.
    """
    match = _ROW_MARKER_RE.search(line)
    if not match:
        return None
    fields = dict(_FIELD_RE.findall(match.group(1)))
    try:
        outcome = FixOutcome(fields["out"]) if fields.get("out") else None
    except ValueError:
        outcome = None
    return RowRecord(
        key=fields.get("k", ""),
        location=fields.get("loc", ""),
        text_key=fields.get("txt", ""),
        outcome=outcome,
        digest=fields.get("d", ""),
    )


def row_is_intact(line: str, record: RowRecord) -> bool:
    """Whether the row still reads exactly as we wrote it."""
    return bool(record.digest) and record.digest == fingerprint(unmarked(line))


def stamp_reply(body: str) -> str:
    """A generated reply with the marker that lets a later round recognise it.

    Trailing rather than leading: `settlement` and `verdict_kind` read a reply's
    opening, and a marker there would hide every verdict behind it.
    """
    text = normalise(body)
    return f"{text}\n\n<!-- pr-comments:generated sha={fingerprint(text)} -->"


def reply_marker_intact(body: str) -> bool | None:
    """Whether a reply's marker still vouches for its text.

    None when there is no marker at all, which only the reply's age can
    interpret — see `thread_replies.is_generated_reply`. False when there is one
    and the text was changed after it was written, wherever the change is: text
    a person adds after the marker is caught by the marker no longer closing the
    body, and anything before it changes the digest.
    """
    matches = list(_REPLY_MARKER_RE.finditer(body))
    if not matches:
        return None
    last = matches[-1]
    if normalise(body[last.end():]):
        return False
    # `bool(...)` rejects an empty captured digest as not intact. That is
    # exactly what `stamp_reply` would write for a reply whose text normalises
    # to "" (see `fingerprint`'s empty-maps-to-empty rule) — unreachable in
    # practice because a generated reply's text is never empty, but if it ever
    # were, this would read it as a person's rather than as ours.
    return bool(last.group(1)) and last.group(1) == fingerprint(body[: last.start()])
