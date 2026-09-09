"""Reading the markdown a knowledge base is made of."""

# doc-group: platform

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from pathlib import Path

from .paths import RAW_DIR

# A setting line in SCHEMA.md: `- key: value`, or `key: value` in a fenced block.
_SETTING_RE = re.compile(r"^\s*-?\s*([a-z_]+):\s*(\S+)\s*$")

_FRONTMATTER_RE = re.compile(r"\A---\r?\n(.*?)\r?\n---\r?\n?", re.DOTALL)

_WIKILINK_RE = re.compile(r"\[\[([^\]|]+?)(?:\|[^\]]*?)?\]\]")

_MD_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")

_CONTRADICTION_RE = re.compile(r"\[CONTRADICTION\]", re.IGNORECASE)

_LOG_UNPROCESSED_RE = re.compile(r"^\s*[-*]?\s*(SESSION_OBSERVATION|QUERY_GAP)\b")

# Log lines open with a bracketed date, so the event keyword is matched on its own
# word boundary rather than anchored: `[2024-01-01] COMPILE: ...`. Testing for the
# keyword anywhere in the line instead would let RECOMPILE answer for COMPILE.
_LOG_EVENT_TEMPLATE = r"(?<![A-Z_])%s:"

_RELATED_HEADING_RE = re.compile(r"^#{1,6}\s*(related|relationships|see also)\s*$", re.IGNORECASE)

_HEADING_RE = re.compile(r"^#{1,6}\s")

# Enough of YAML for article frontmatter, which is flat scalars plus one list
# form. A real parser would mean a dependency for two shapes that have never
# varied; anything more complex than these is left to the schema's own tooling.
_YAML_KV_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_-]*):\s*(.*)$")

_YAML_ITEM_RE = re.compile(r"^\s*-\s+(.*)$")

HASH_PREFIX_LEN = 8

HASH_CHUNK_BYTES = 65536

# A recorded hash is hex. Used to tell a manifest's header row from a real entry.
_HEX_RE = re.compile(r"[0-9a-f]+", re.IGNORECASE)



# ── Parsing helpers ─────────────────────────────────────────────────────────


def read_text(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, UnicodeDecodeError):
        return None


def _source_frontmatter(path: Path) -> dict:
    """Frontmatter of a markdown source; empty for every other source type."""
    if path.suffix.lower() != ".md":
        return {}
    text = read_text(path)
    return {} if text is None else _split_frontmatter(text)[0]


def _hash_file(path: Path) -> str:
    """The sha256 prefix the compile step uses to detect changed sources.

    Read in chunks rather than whole: `raw/` holds whatever was ingested, and a
    repo snapshot or a large PDF should not be resident in memory to be hashed.
    """
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            _consume(handle, digest)
    except OSError:
        return ""
    return digest.hexdigest()[:HASH_PREFIX_LEN]


def _consume(handle, digest) -> None:
    for chunk in iter(lambda: handle.read(HASH_CHUNK_BYTES), b""):
        digest.update(chunk)


def _strip_frontmatter(text: str) -> str:
    return _FRONTMATTER_RE.sub("", text, count=1)


def _split_frontmatter(text: str) -> tuple[dict, str]:
    match = _FRONTMATTER_RE.match(text)
    if match is None:
        return {}, text
    return _parse_frontmatter(match.group(1)), text[match.end():]


def _parse_frontmatter(block: str) -> dict:
    """Parse the flat-scalar-and-list subset of YAML that article frontmatter uses."""
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
            data[key] = [_scalar(p) for p in inner.split(",") if p.strip()] if inner else []
        else:
            data[key] = _scalar(raw)
    return data


def _append_item(data: dict, key: str, value: str) -> None:
    """Add a block-list item, ignoring it when the key already holds a scalar."""
    existing = data.setdefault(key, [])
    if isinstance(existing, list):
        existing.append(value)


def _scalar(raw: str) -> str:
    return raw.strip().strip("\"'").strip()


def _parse_date(value) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    raw = value.strip().strip("\"'")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed


def _slugify(text: str) -> str:
    """Normalise a link target or title to the slug an article file is named by."""
    text = text.strip()
    if text.endswith(".md"):
        text = text[:-3]
    text = text.rsplit("/", 1)[-1]
    text = re.sub(r"[^\w\s-]", "", text.lower())
    return re.sub(r"[\s_]+", "-", text).strip("-")


def _is_table_furniture(path: str, digest: str) -> bool:
    """True for a manifest table's header row or its `---` separator.

    Both are rows by the parser's reckoning, and the header in particular reads
    as a source named `source` with the hash `hash` — a phantom entry that would
    report a real source of that name as compiled, against a hash that is a word.
    """
    if not path:
        return True
    if set(path) <= set("-: "):
        return True
    return path.strip().lower() == "source" and not _HEX_RE.fullmatch(digest.strip())


def _normalise_source_path(path: str) -> str:
    """Bring a manifest path to the same spelling as `Source.rel`.

    The manifest is written by hand and by the compile step, so a source turns
    up as `note.md`, `raw/note.md`, or `./raw/note.md`. All three mean the file
    at `raw/note.md`, which is the form lookups use, so normalise toward that
    rather than away from it — stripping the prefix instead would leave every
    manifest key unable to match the source it records.
    """
    cleaned = path.strip().lstrip("./").strip()
    if not cleaned:
        return ""
    if not cleaned.startswith(f"{RAW_DIR}/"):
        cleaned = f"{RAW_DIR}/{cleaned}"
    return cleaned


