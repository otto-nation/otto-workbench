"""Where a knowledge base lives on disk, and what marks one as such."""

# doc-group: platform

from __future__ import annotations

import re
from pathlib import Path

# SCHEMA.md is what makes a directory a knowledge base. The plugin this was
# ported from used two different markers — `_index.md` in its session hook and
# SCHEMA.md in its init flow — so a wiki mid-build answered "yes" to one and
# "no" to the other. The index is generated and can be deleted and rebuilt; the
# schema is authored and cannot.
SCHEMA_FILE = "SCHEMA.md"
INDEX_FILE = "_index.md"
SOURCES_FILE = "_sources.md"
LOG_FILE = "_log.md"

ARTICLES_DIR = "articles"
RAW_DIR = "raw"
DRAFTS_DIR = "drafts"
ARCHIVE_DIR = "archive"
META_DIR = "meta"

DEFAULT_WIKI_DIRNAME = "wiki"

# How far up from cwd to look. The search stops at a repo root when there is
# one, so this only bounds the walk outside a repo.
MAX_PARENT_DEPTH = 8

# Schema settings this script reads, and the value each falls back to when the
# schema does not say. Every one is overridable in SCHEMA.md; the defaults are
# the plugin's, kept so an existing wiki behaves the same after the port.
DEFAULT_SETTINGS = {
    "min_article_words": 150,
    "staleness_threshold_days": 180,
}








def _utc_now() -> datetime:
    return datetime.now(timezone.utc)



# ── Resolution ──────────────────────────────────────────────────────────────


def is_wiki(path: Path) -> bool:
    return (path / SCHEMA_FILE).is_file()


def find_wiki(start: Path, explicit: str | None = None, dirname: str | None = None) -> Path | None:
    """Locate the knowledge base governing *start*.

    Three steps, in order: an explicit path, then the configured directory name
    at each level from *start* up to the repo root, then *start* itself if it is
    a wiki. An explicit path that is not a wiki resolves to nothing rather than
    falling back to the walk — it names one specific base, and silently using a
    different one is how the wrong wiki gets written to.

    The plugin this replaces consulted a global registry as a fallback and
    returned its first entry, so a session in one project got another project's
    wiki.

    *dirname* is passed in rather than read here: resolving `wiki.dir` needs
    `config` and `git`, which sit at this layer and so cannot be imported from
    it. The CLI resolves the name and hands it down, which also keeps this
    function answerable without a config file at all.
    """
    if explicit:
        candidate = Path(explicit).expanduser().resolve()
        return candidate if is_wiki(candidate) else None

    start = start.resolve()
    name = dirname or DEFAULT_WIKI_DIRNAME
    for depth, directory in enumerate([start, *start.parents]):
        if depth > MAX_PARENT_DEPTH:
            break
        if is_wiki(directory / name):
            return directory / name
        if is_wiki(directory):
            return directory
        if (directory / ".git").exists():
            break
    return None


