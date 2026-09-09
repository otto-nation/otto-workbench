"""Layer 2 — knowledge base store. May import: core."""

from .model import Article, Source, Wiki
from .paths import (
    ARCHIVE_DIR,
    ARTICLES_DIR,
    DEFAULT_SETTINGS,
    DEFAULT_WIKI_DIRNAME,
    DRAFTS_DIR,
    INDEX_FILE,
    LOG_FILE,
    MAX_PARENT_DEPTH,
    RAW_DIR,
    SCHEMA_FILE,
    SOURCES_FILE,
    find_wiki,
    is_wiki,
)
from .parsing import HASH_PREFIX_LEN, read_text
from .report import build_index, collect_lint, collect_status

__all__ = [
    "ARCHIVE_DIR",
    "ARTICLES_DIR",
    "Article",
    "DEFAULT_SETTINGS",
    "DEFAULT_WIKI_DIRNAME",
    "DRAFTS_DIR",
    "HASH_PREFIX_LEN",
    "INDEX_FILE",
    "LOG_FILE",
    "MAX_PARENT_DEPTH",
    "RAW_DIR",
    "SCHEMA_FILE",
    "SOURCES_FILE",
    "Source",
    "Wiki",
    "build_index",
    "collect_lint",
    "collect_status",
    "find_wiki",
    "is_wiki",
    "read_text",
]
