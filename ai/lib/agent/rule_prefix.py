"""Assemble a Pi system-prompt prefix from a seeded ``rules_home``.

Pi has no ``CLAUDE_CONFIG_DIR`` equivalent that relocates *only* the operator
rule prefix: ``PI_CODING_AGENT_DIR`` also moves settings, sessions and
extensions (the Vertex provider and the ``gh_*`` tools). The eval therefore
keeps ``--no-context-files`` — so the operator's ~39k ``AGENTS.md`` does not
load — and injects the arm with ``--append-system-prompt``.

The files to inject are ``rules_home/rules/*.md``, concatenated the way
``step_pi_guidelines`` builds ``~/.pi/agent/AGENTS.md``: YAML frontmatter is
stripped, a ``paths:`` scope is dropped (Pi has no path-conditional context),
and a ``harness:`` list that omits ``pi`` is dropped. Empty ``rules_home`` is
the caller's problem; a *set* but unreadable or empty home is an error, never
a silent empty prompt.
"""

# doc-group: backend

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)
_YAML_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")
_YAML_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")


class RulePrefixError(ValueError):
    """``rules_home`` was set but could not be turned into a Pi prompt prefix."""


def rule_blob(rules_home: str) -> str:
    """The concatenated Pi-reachable bodies under ``rules_home/rules/``.

    ``rules_home`` is a directory (the seeded arm). The path must be absolute
    after ``~`` expansion — the same contract Claude Code enforces — and the
    ``rules/`` subdir must exist, be readable, and yield at least one body
    after ``_pi_rule_reaches_pi`` filtering.
    """
    home = Path(rules_home).expanduser()
    if not home.is_absolute():
        raise RulePrefixError(
            f"AgentInvocation.rules_home must be absolute; got {rules_home!r}"
        )
    rules_dir = home / "rules"
    if not home.is_dir() or not rules_dir.is_dir():
        raise RulePrefixError(
            f"rules_home is not a readable directory with a rules/ subdir: {home}"
        )
    try:
        files = sorted(rules_dir.glob("*.md"))
    except OSError as exc:
        raise RulePrefixError(
            f"rules_home/rules/ is unreadable: {rules_dir}"
        ) from exc
    if not files:
        raise RulePrefixError(
            f"rules_home/rules/ contains no .md files: {rules_dir}"
        )
    parts = [_rule_section(path) for path in files]
    kept = [part for part in parts if part is not None]
    if not kept:
        raise RulePrefixError(
            f"no rule in {rules_dir} reaches Pi "
            f"(every file is path-scoped or harness-opted-out)"
        )
    return "\n".join(kept)


def materialize(rules_home: str) -> str:
    """Write ``rule_blob`` to a temp file and return its path.

    Pi's ``--append-system-prompt`` accepts text *or* an existing file path
    (``resolvePromptInput`` reads the file when ``existsSync`` is true). The
    blob is large enough that the file form is the one to use; the temp file
    is not under ``~/.claude`` or ``~/.pi``.
    """
    blob = rule_blob(rules_home)
    fd, path = tempfile.mkstemp(prefix="pi-rules-", suffix=".md", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(blob)
    except OSError:
        os.unlink(path)
        raise
    return path


def _rule_section(path: Path) -> str | None:
    """One file's banner plus body, or None when the file does not reach Pi."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise RulePrefixError(f"could not read rule file: {path}") from exc
    frontmatter, body = _split_frontmatter(text)
    if not _reaches_pi(frontmatter):
        return None
    return f"<!-- ─── {path.name} ─── -->\n\n{body.rstrip()}\n"


def _split_frontmatter(text: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    return _parse_frontmatter(match.group(1)), text[match.end():]


def _parse_frontmatter(block: str) -> dict:
    """The flat-scalar-and-list YAML subset rule files actually write."""
    data: dict = {}
    key: str | None = None
    for line in block.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        item = _YAML_ITEM_RE.match(line)
        if item is not None and key is not None:
            _append_item(data, key, _scalar(item.group(1)))
            continue
        match = _YAML_KV_RE.match(line)
        if match is None:
            continue
        key = match.group(1)
        raw = match.group(2).strip()
        if not raw:
            data[key] = []
        elif raw.startswith("[") and raw.endswith("]"):
            inner = raw[1:-1].strip()
            data[key] = (
                [_scalar(part) for part in inner.split(",") if part.strip()]
                if inner else []
            )
        else:
            data[key] = _scalar(raw)
    return data


def _append_item(data: dict, key: str, value: str) -> None:
    existing = data.setdefault(key, [])
    if isinstance(existing, list):
        existing.append(value)


def _scalar(raw: str) -> str:
    return raw.strip().strip("\"'").strip()


def _reaches_pi(frontmatter: dict) -> bool:
    """The Python form of ``_pi_rule_reaches_pi`` in ``ai/pi/steps.sh``.

    A ``paths:`` list scopes the rule to matching files under Claude Code; Pi
    has no path-conditional context, so those files are omitted rather than
    applied everywhere. ``paths: []`` is not a scope. A ``harness:`` list that
    omits ``pi`` (or names nothing) is the same opt-out ``rule_harness_ok``
    enforces.
    """
    if _as_list(frontmatter.get("paths")):
        return False
    harnesses = _as_list(frontmatter.get("harness"), absent=None)
    if harnesses is None:
        return True
    return "pi" in harnesses


def _as_list(value, absent=()) -> list[str] | None:
    """A frontmatter list field as strings; ``absent`` when the key was missing.

    Distinguishes a missing ``harness:`` (every harness) from ``harness: []``
    (no harness) — the same distinction ``frontmatter_field`` keeps the
    brackets for.
    """
    if value is None:
        return absent
    if isinstance(value, list):
        return [str(item) for item in value if str(item)]
    text = str(value).strip()
    return [text] if text else []
