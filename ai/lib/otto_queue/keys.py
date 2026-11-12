"""Idempotency keys: the identity that makes two requests for paid work the same request.

A review or a comment pass costs about $1–13, so the same one for the same
inputs runs once. A request whose key matches a running entry joins it, and
one matching a `done` entry gets its result (`otto_queue.ledger`). There is no
time window: the inputs are the whole identity.

| Field | Normalised as |
|---|---|
| action | inner whitespace collapsed (`review`, `review --self --fix`, `comments`) |
| repo | `owner/name`, lowercased — GitHub compares it case-insensitively |
| PR | a positive integer |
| head and base SHA | full 40- or 64-hex, lowercased; an abbreviation is refused, since two abbreviations of one commit would be two keys |
| focus | whitespace collapsed |
| effort, model | stripped; effort lowercased |
| tool version | `core.version.tool_version()` unless given |

An absent flag and an empty one are the same. Callers pass *resolved*
values — the effort and model the run will actually use, not the flags as
typed — or a default spelled out and a default left implicit become two keys
for one run.

The key is `review:` plus the SHA-256 of the fields' canonical JSON: fixed
length, and it does not put focus text into `otto-queue status`.
"""

# doc-group: queue

from __future__ import annotations

import hashlib
import json
import re

import core.version

KEY_PREFIX = "review"
_FULL_SHA = re.compile(r"[0-9a-f]{40}|[0-9a-f]{64}")


def _collapse(text: str | None) -> str:
    return " ".join((text or "").split())


def _full_sha(name: str, value: str) -> str:
    sha = value.strip().lower()
    if not _FULL_SHA.fullmatch(sha):
        raise ValueError(f"{name} must be a full commit SHA, got {value!r}")
    return sha


def review_key(*, action: str, repo: str, pr_number: int, head_sha: str, base_sha: str,
               focus: str | None = None, effort: str | None = None, model: str | None = None,
               tool_version: str | None = None) -> str:
    """The idempotency key for one paid review or comment pass; see the module docstring.

    ValueError for an empty action, a repo without an owner, a PR number below 1,
    or a SHA that is not a full object name.
    """
    if not _collapse(action) or "/" not in repo.strip():
        raise ValueError(f"an action and an owner/repo are required, got {action!r} and {repo!r}")
    if pr_number < 1:
        raise ValueError(f"the PR number must be positive, got {pr_number}")
    fields = {
        "action": _collapse(action),
        "repo": repo.strip().lower(),
        "pr": int(pr_number),
        "head_sha": _full_sha("head_sha", head_sha),
        "base_sha": _full_sha("base_sha", base_sha),
        "focus": _collapse(focus),
        "effort": (effort or "").strip().lower(),
        "model": (model or "").strip(),
        "tool_version": tool_version if tool_version is not None else core.version.tool_version(),
    }
    canonical = json.dumps(fields, sort_keys=True, separators=(",", ":"))
    return f"{KEY_PREFIX}:{hashlib.sha256(canonical.encode()).hexdigest()}"
