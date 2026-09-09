"""Making a knowledge base, and putting a source into one."""

# doc-group: platform

from __future__ import annotations

import re
import shutil
from datetime import datetime, timezone
from pathlib import Path

from .parsing import hash_file
from .paths import (
    ARCHIVE_DIR,
    ARTICLES_DIR,
    DRAFTS_DIR,
    INDEX_FILE,
    LOG_FILE,
    META_DIR,
    RAW_DIR,
    SCHEMA_FILE,
    SOURCES_FILE,
    is_wiki,
)

# Created empty by init, in the order a reader meets them.
LAYOUT = (RAW_DIR, ARTICLES_DIR, DRAFTS_DIR, ARCHIVE_DIR, META_DIR)

# The manifest's two leading columns are the ones `Wiki.recorded_source_hashes`
# reads; the rest are for a person. Written here so init and the compile step
# cannot disagree about the shape.
SOURCES_HEADER = (
    "# Source Manifest\n\n"
    "Hashes come from `wiki sources`. A hash written by hand is a guess.\n\n"
    "| Source | Hash | Type | Ingested | Articles |\n"
    "| --- | --- | --- | --- | --- |\n"
)

INDEX_PLACEHOLDER = "# Index\n\n_No articles yet._\n"

GLOSSARY_PLACEHOLDER = "# Glossary\n\nTerms this knowledge base uses in a specific sense.\n"

# A slug is lowercase, hyphenated, and short enough to stay readable in a
# directory listing. Long titles are cut at a word boundary where one is near
# the limit, because a slug ending mid-word reads as a truncated file.
SLUG_MAX_LEN = 60
_SLUG_STRIP_RE = re.compile(r"[^\w\s-]")
_SLUG_SPACE_RE = re.compile(r"[\s_]+")

# A source type reaches the filename and the frontmatter, so it is held to the
# same shape as a slug. Left free-form, a `/` or `..` in it walks the staged file
# out of `raw/`, and a newline opens a second frontmatter key.
DEFAULT_SOURCE_TYPE = "file"


class WikiExistsError(Exception):
    """A knowledge base is already there, and init will not write over it."""


def clean_source_type(source_type: str) -> str:
    """*source_type* reduced to the slug shape, or the default when nothing is left.

    Applied for the same reason titles are: the value is interpolated into a
    path and into YAML, and it arrives from `--type` unvalidated.
    """
    cleaned = slugify_title(source_type)
    return DEFAULT_SOURCE_TYPE if cleaned == "untitled" else cleaned


def slugify_title(title: str) -> str:
    """A filename stem for *title*, cut at a word boundary when it must be cut."""
    cleaned = _SLUG_SPACE_RE.sub("-", _SLUG_STRIP_RE.sub("", title.lower())).strip("-")
    if len(cleaned) <= SLUG_MAX_LEN:
        return cleaned or "untitled"
    cut = cleaned[:SLUG_MAX_LEN]
    boundary = cut.rfind("-")
    # Only honour the boundary if it leaves most of the budget used; otherwise a
    # title whose first word is long would slug down to that one word.
    if boundary >= SLUG_MAX_LEN // 2:
        cut = cut[:boundary]
    return cut.strip("-") or "untitled"


def init_wiki(root: Path, domain: str = "", audience: str = "", template: Path | None = None) -> Path:
    """Create the knowledge base at *root* and return it.

    Raises ``WikiExistsError`` when one is already there. Init is otherwise
    idempotent in the sense that matters: it writes only files that do not
    exist, so a base half-created by an interrupted run completes rather than
    losing what it already had.
    """
    if is_wiki(root):
        raise WikiExistsError(f"{root / SCHEMA_FILE} already exists")

    for name in LAYOUT:
        (root / name).mkdir(parents=True, exist_ok=True)

    _write_if_absent(root / SCHEMA_FILE, _render_schema(template, domain, audience))
    _write_if_absent(root / INDEX_FILE, INDEX_PLACEHOLDER)
    _write_if_absent(root / SOURCES_FILE, SOURCES_HEADER)
    _write_if_absent(root / META_DIR / "glossary.md", GLOSSARY_PLACEHOLDER)
    _write_if_absent(
        root / LOG_FILE,
        f"# Activity Log\n\n[{_today()}] INIT: {domain or 'knowledge base created'}\n",
    )
    return root


def stage_source(root: Path, source: Path, source_type: str = "file", title: str = "") -> Path:
    """Copy *source* into `raw/` with frontmatter, and return where it landed.

    The mechanical half of ingest: the copy, the name, the frontmatter, and the
    log line. Reading the source and turning it into articles is the skill's
    work — this exists so the file lands somewhere `wiki sources` can hash it,
    rather than being written from a model's recollection of its contents.

    No `content_hash` is written. `wiki sources` computes hashes from file
    bytes; recording one here would be a second copy to fall out of date.
    """
    if not source.is_file():
        raise FileNotFoundError(source)

    kind = clean_source_type(source_type)
    stem = slugify_title(title or source.stem)
    target = _unused_path(root / RAW_DIR / f"{kind}-{stem}{source.suffix or '.md'}")
    target.parent.mkdir(parents=True, exist_ok=True)

    if source.suffix.lower() in {".md", ".markdown", ".txt", ".rst"}:
        # A source carrying its own frontmatter keeps it, in the body, below the
        # ingest block. Only the first block is parsed back out, and the
        # original is part of what was ingested.
        target.write_text(
            _frontmatter(kind, title or source.stem, source)
            + source.read_text(encoding="utf-8", errors="replace"),
            encoding="utf-8",
        )
    else:
        # Anything else is copied byte for byte: wrapping a PDF or an image in
        # markdown frontmatter would corrupt it, and the manifest records the
        # type either way.
        shutil.copy2(source, target)

    append_log(root, f"INGEST: {source} → {target.relative_to(root).as_posix()}")
    return target


def _yaml_scalar(value: str) -> str:
    """*value* as a double-quoted YAML scalar, on one line."""
    flattened = " ".join(str(value).split())
    return '"' + flattened.replace("\\", "\\\\").replace('"', '\\"') + '"'


def append_log(root: Path, message: str) -> None:
    """Add a dated line to the activity log, creating it when absent."""
    path = root / LOG_FILE
    existing = path.read_text(encoding="utf-8") if path.exists() else "# Activity Log\n"
    if not existing.endswith("\n"):
        existing += "\n"
    path.write_text(f"{existing}[{_today()}] {message}\n", encoding="utf-8")


def manifest_row(root: Path, staged: Path, source_type: str) -> str:
    """The `_sources.md` row for a staged file, with a hash that was computed."""
    rel = staged.relative_to(root).as_posix()
    return f"| {rel} | {hash_file(staged)} | {clean_source_type(source_type)} | {_today()} | |\n"


def _render_schema(template: Path | None, domain: str, audience: str) -> str:
    if template is not None and template.is_file():
        text = template.read_text(encoding="utf-8")
    else:
        text = "# Wiki Schema\n\n## Identity\n\nA knowledge base about {DOMAIN}, for {AUDIENCE}.\n"
    return text.replace("{DOMAIN}", domain or "this project").replace(
        "{AUDIENCE}", audience or "whoever works on it"
    )


def _frontmatter(source_type: str, title: str, source: Path) -> str:
    return (
        "---\n"
        f"source_type: {source_type}\n"
        f"title: {_yaml_scalar(title)}\n"
        f"original_path: {_yaml_scalar(str(source))}\n"
        f"ingest_date: {_today()}\n"
        "---\n\n"
    )


def _unused_path(path: Path) -> Path:
    """*path*, or the first `-2`, `-3`, ... variant that is free.

    Ingesting the same filename twice is a real case — two `README.md` from
    different repos — and silently overwriting the first would lose a source
    the manifest still claims is there.
    """
    if not path.exists():
        return path
    for n in range(2, 100):
        candidate = path.with_name(f"{path.stem}-{n}{path.suffix}")
        if not candidate.exists():
            return candidate
    raise FileExistsError(path)


def _write_if_absent(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content, encoding="utf-8")


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()
