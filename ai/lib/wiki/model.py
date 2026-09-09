"""The knowledge base as objects: articles, sources, and the store over them."""

# doc-group: platform

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .paths import (
    ARCHIVE_DIR,
    ARTICLES_DIR,
    DEFAULT_SETTINGS,
    DRAFTS_DIR,
    INDEX_FILE,
    LOG_FILE,
    RAW_DIR,
    SCHEMA_FILE,
    SOURCES_FILE,
)
from .parsing import (
    _HEADING_RE,
    _RELATED_HEADING_RE,
    _SETTING_RE,
    _WIKILINK_RE,
    _MD_LINK_RE,
    _LOG_UNPROCESSED_RE,
    _LOG_EVENT_TEMPLATE,
    hash_file,
    parse_query_gap,
    _normalise_source_path,
    _is_table_furniture,
    _parse_date,
    read_text,
    _slugify,
    _source_frontmatter,
    _split_frontmatter,
    _strip_frontmatter,
)

# ── Store ───────────────────────────────────────────────────────────────────


@dataclass
class Article:
    """One compiled article, with the frontmatter and links already extracted."""

    path: Path
    root: Path
    frontmatter: dict = field(default_factory=dict)
    body: str = ""

    @property
    def slug(self) -> str:
        return self.path.stem

    @property
    def rel(self) -> str:
        return self.path.relative_to(self.root).as_posix()

    @property
    def title(self) -> str:
        title = self.frontmatter.get("title")
        return title if isinstance(title, str) and title else self.slug

    @property
    def is_draft(self) -> bool:
        return DRAFTS_DIR in self.path.relative_to(self.root).parts

    @property
    def is_archived(self) -> bool:
        return ARCHIVE_DIR in self.path.relative_to(self.root).parts

    @property
    def word_count(self) -> int:
        return len(self.body.split())

    @property
    def updated(self) -> datetime | None:
        for key in ("updated", "created", "date"):
            parsed = _parse_date(self.frontmatter.get(key))
            if parsed is not None:
                return parsed
        return None

    @property
    def links(self) -> list[str]:
        """Wikilink targets, normalised to slugs, in order of appearance."""
        return [_slugify(m.group(1)) for m in _WIKILINK_RE.finditer(self.body)]

    @property
    def tags(self) -> list[str]:
        tags = self.frontmatter.get("tags")
        if isinstance(tags, list):
            return [str(t).strip().lower() for t in tags if str(t).strip()]
        if isinstance(tags, str):
            return [t.strip().lower() for t in re.split(r"[,\s]+", tags) if t.strip()]
        return []

    def related_section_links(self) -> list[str] | None:
        """Wikilinks under the Related heading, or None when there is no such heading."""
        lines = self.body.splitlines()
        start = next((i for i, line in enumerate(lines) if _RELATED_HEADING_RE.match(line)), None)
        if start is None:
            return None
        section: list[str] = []
        for line in lines[start + 1:]:
            if _HEADING_RE.match(line):
                break
            section.append(line)
        return [_slugify(m.group(1)) for m in _WIKILINK_RE.finditer("\n".join(section))]


@dataclass
class Source:
    """One raw source file, with the hash the compile step keys off."""

    path: Path
    root: Path
    frontmatter: dict = field(default_factory=dict)
    content_hash: str = ""

    @property
    def rel(self) -> str:
        return self.path.relative_to(self.root).as_posix()


class Wiki:
    """A knowledge base on disk, loaded once and read many times."""

    def __init__(self, root: Path):
        self.root = root
        self._articles: list[Article] | None = None
        self._sources: list[Source] | None = None
        self._settings: dict | None = None

    # -- configuration

    @property
    def settings(self) -> dict:
        if self._settings is None:
            self._settings = self._load_settings()
        return self._settings

    def _load_settings(self) -> dict:
        settings = dict(DEFAULT_SETTINGS)
        text = read_text(self.root / SCHEMA_FILE)
        if text is None:
            return settings
        for line in text.splitlines():
            match = _SETTING_RE.match(line)
            if match is None:
                continue
            key, raw = match.group(1), match.group(2).strip().strip("\"'")
            if key not in DEFAULT_SETTINGS:
                continue
            try:
                settings[key] = int(raw)
            except ValueError:
                continue
        return settings

    def domain(self) -> str | None:
        """The wiki's subject: the first line of prose in SCHEMA.md.

        Headings, rules, and HTML comments are skipped. The comment case is not
        hypothetical — the shipped template opens with an editing note, and
        reading it as the domain put that note in every status report.
        """
        text = read_text(self.root / SCHEMA_FILE)
        if text is None:
            return None
        for line in _strip_frontmatter(text).splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith(("#", "---", "<!--")):
                continue
            return stripped.lstrip("-* ").strip()
        return None

    # -- content

    @property
    def articles(self) -> list[Article]:
        if self._articles is None:
            self._articles = self._load_articles()
        return self._articles

    def published(self) -> list[Article]:
        """Articles in `articles/` — not drafts, not archived."""
        return [a for a in self.articles if not a.is_draft and not a.is_archived]

    def drafts(self) -> list[Article]:
        return [a for a in self.articles if a.is_draft]

    def _load_articles(self) -> list[Article]:
        bases = [self.root / s for s in (ARTICLES_DIR, DRAFTS_DIR, ARCHIVE_DIR)]
        paths = [p for base in bases if base.is_dir() for p in sorted(base.rglob("*.md"))]
        return [a for a in (self._read_article(p) for p in paths) if a is not None]

    def _read_article(self, path: Path) -> Article | None:
        text = read_text(path)
        if text is None:
            return None
        frontmatter, body = _split_frontmatter(text)
        return Article(path=path, root=self.root, frontmatter=frontmatter, body=body)

    @property
    def sources(self) -> list[Source]:
        if self._sources is None:
            self._sources = self._load_sources()
        return self._sources

    def _load_sources(self) -> list[Source]:
        base = self.root / RAW_DIR
        if not base.is_dir():
            return []
        paths = [
            p for p in sorted(base.rglob("*")) if p.is_file() and not p.name.startswith(".")
        ]
        return [self._read_source(p) for p in paths]

    def _read_source(self, path: Path) -> Source:
        return Source(
            path=path,
            root=self.root,
            frontmatter=_source_frontmatter(path),
            content_hash=hash_file(path),
        )

    # -- bookkeeping files

    def index_targets(self) -> set[str]:
        """Article slugs the master index links to, by wikilink or markdown link."""
        text = read_text(self.root / INDEX_FILE)
        if text is None:
            return set()
        targets = {_slugify(m.group(1)) for m in _WIKILINK_RE.finditer(text)}
        for match in _MD_LINK_RE.finditer(text):
            target = match.group(1).split("#", 1)[0].strip()
            if target.endswith(".md"):
                targets.add(_slugify(Path(target).stem))
        return targets

    def recorded_source_hashes(self) -> dict[str, str]:
        """Source path to recorded hash, from the `_sources.md` manifest.

        The manifest is a markdown table the compile step maintains. Rows are
        read positionally from the first two columns: path, then hash.
        """
        text = read_text(self.root / SOURCES_FILE)
        if text is None:
            return {}
        recorded: dict[str, str] = {}
        for line in text.splitlines():
            if not line.strip().startswith("|"):
                continue
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) < 2:
                continue
            path, digest = cells[0].strip("`"), cells[1].strip("`")
            if _is_table_furniture(path, digest):
                continue
            recorded[_normalise_source_path(path)] = digest.lower()
        return recorded

    def unprocessed_log_entries(self) -> list[str]:
        text = read_text(self.root / LOG_FILE)
        if text is None:
            return []
        return [line.strip() for line in text.splitlines() if _LOG_UNPROCESSED_RE.match(line)]

    def query_gaps(self) -> list[tuple[str, str]]:
        """Every recorded QUERY_GAP as (date, question), oldest first.

        These are what the wiki was asked and could not answer well. `lint`
        counts them; this returns them, which is the difference between knowing
        the wiki has gaps and knowing what they are about.
        """
        text = read_text(self.root / LOG_FILE)
        if text is None:
            return []
        parsed = (parse_query_gap(line) for line in text.splitlines())
        return [gap for gap in parsed if gap is not None and gap[1]]

    def last_log_event(self, kind: str) -> str | None:
        """The most recent `KIND:` line in the activity log."""
        text = read_text(self.root / LOG_FILE)
        if text is None:
            return None
        pattern = re.compile(_LOG_EVENT_TEMPLATE % re.escape(kind.upper()))
        matches = [ln.strip() for ln in text.splitlines() if pattern.search(ln.upper())]
        return matches[-1] if matches else None


