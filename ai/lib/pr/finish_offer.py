"""Whether a turn ended by offering to open a PR instead of opening it.

``offers_pr(message)`` is true when *message* asks or invites the user to have a
PR opened ("want me to open the draft PR", "say if you want the PR") — the
ending ``ai/guidelines/rules/general.md`` § Workflow forbids once a branch's
implementation is complete. ``last_assistant_text(transcript)`` is the text of
the last assistant message in a Claude Code transcript. ``should_block(message,
cwd)`` is true when the turn offered a PR while *cwd* is on a feature branch with
no open PR; a branch that is detached, is the default branch, or whose PR lookup
failed is never blocked, because the guard only holds a rule it can see broken.
"""

# ceiling: the offer is a phrase match on the last message; upgrade to a model
# judgement if reworded offers slip through the pattern below.

# doc-group: platform

from __future__ import annotations

import json
import re
from pathlib import Path

import gh.client
import git.client
import git.topology

_LEAD = r"(?:want me to|shall i|should i|say if you want|let me know if you|if you(?:'d)? (?:like|want)|ready for me to)"
_ACT = r"(?:open|create|file|raise|submit|make)"
_PR = r"(?:pr|pull request)"
OFFER = re.compile(rf"\b{_LEAD}\b[^.\n]{{0,80}}\b{_ACT}\b[^.\n]{{0,40}}\b{_PR}\b", re.IGNORECASE)
# "say if you want the PR" has no verb between the invitation and the noun.
BARE_OFFER = re.compile(rf"\b(?:say|tell me) if you want\b[^.\n]{{0,40}}\b{_PR}\b", re.IGNORECASE)


def offers_pr(message: str) -> bool:
    return bool(OFFER.search(message) or BARE_OFFER.search(message))


def _text_of(entry: dict) -> str:
    message = entry.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    blocks = content if isinstance(content, list) else []
    return "\n".join(b["text"] for b in blocks if isinstance(b, dict) and isinstance(b.get("text"), str))


def _json_entry(line: str) -> dict | None:
    try:
        value = json.loads(line)
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


def last_assistant_text(transcript: str | Path) -> str:
    """The text of the transcript's last assistant entry, "" when there is none or it cannot be read."""
    try:
        lines = Path(transcript).read_text().splitlines()
    except OSError:
        return ""
    entries = (_json_entry(line) for line in reversed(lines))
    texts = (_text_of(e) for e in entries if e is not None and e.get("type") == "assistant")
    return next((text for text in texts if text), "")


def should_block(message: str, cwd: str | Path) -> bool:
    if not offers_pr(message):
        return False
    branch = git.client.current_branch(cwd)
    if branch in ("", "HEAD") or branch == git.topology.default_branch(cwd):
        return False
    found = gh.client.json_out("pr", "list", "--head", branch, "--state", "open",
                               "--json", "number", cwd=cwd, default=None)
    return isinstance(found, list) and not found
