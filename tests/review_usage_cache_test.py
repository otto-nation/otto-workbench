"""Tests for review.usage_cache — per-review-directory session-log usage cache."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import review.usage_cache
from review.paths import (
    ARCHIVE_SESSION_EXT, aggregate_session_usage, archived_session_usage,
    archives_dir,
)
from review.usage_cache import FILENAME_USAGE_CACHE, UsageCache

RESULT = '{"type": "result", "total_cost_usd": 0.5, "usage": {"input_tokens": 7}}\n'


def _log(review_dir: Path, name="session.jsonl", text=RESULT) -> Path:
    review_dir.mkdir(parents=True, exist_ok=True)
    path = review_dir / name
    path.write_text(text)
    return path


def test_a_saved_cache_returns_usage_without_reparsing(tmp_path, monkeypatch):
    review_dir = tmp_path / "rev"
    log = _log(review_dir)
    cache = UsageCache(review_dir)
    first = cache.usage([log])
    cache.save()

    def boom(_path):
        raise AssertionError("session log re-parsed")

    monkeypatch.setattr(review.usage_cache, "parse_session_log", boom)
    second = UsageCache(review_dir).usage([log])
    assert second == first
    assert second.cost == 0.5
    assert second.input_tokens == 7


def test_appending_a_result_line_makes_the_next_cache_reparse(tmp_path):
    review_dir = tmp_path / "rev"
    log = _log(review_dir)
    cache = UsageCache(review_dir)
    cache.usage([log])
    cache.save()
    with log.open("a") as fh:
        fh.write('{"type": "result", "total_cost_usd": 0.25, "usage": {"input_tokens": 1}}\n')
    second = UsageCache(review_dir).usage([log])
    assert second.cost == 0.75
    assert second.input_tokens == 8


def test_a_corrupt_cache_is_ignored_and_replaced_on_save(tmp_path):
    review_dir = tmp_path / "rev"
    log = _log(review_dir)
    (review_dir / FILENAME_USAGE_CACHE).write_text("not json")
    cache = UsageCache(review_dir)
    usage = cache.usage([log])
    cache.save()
    payload = json.loads((review_dir / FILENAME_USAGE_CACHE).read_text())
    assert payload["version"] == 1
    assert usage.cost == 0.5
    assert "session.jsonl" in payload["logs"]


def test_a_malformed_entry_is_reparsed_rather_than_trusted(tmp_path):
    review_dir = tmp_path / "rev"
    log = _log(review_dir)
    st = log.stat()
    (review_dir / FILENAME_USAGE_CACHE).write_text(json.dumps(
        {"version": 1, "logs": {"session.jsonl": {"stat": [st.st_size, st.st_mtime_ns]}}}))
    cache = UsageCache(review_dir)
    assert cache.usage([log]).cost == 0.5
    cache.save()
    payload = json.loads((review_dir / FILENAME_USAGE_CACHE).read_text())
    assert payload["logs"]["session.jsonl"]["usage"]["cost"] == 0.5


def test_save_drops_the_entry_of_a_deleted_log(tmp_path):
    review_dir = tmp_path / "rev"
    log = _log(review_dir)
    cache = UsageCache(review_dir)
    cache.usage([log])
    cache.save()
    log.unlink()
    cache.save()
    payload = json.loads((review_dir / FILENAME_USAGE_CACHE).read_text())
    assert payload["logs"] == {}


def test_after_save_the_cache_mtime_matches_the_log(tmp_path):
    review_dir = tmp_path / "rev"
    log = _log(review_dir)
    cache = UsageCache(review_dir)
    cache.usage([log])
    cache.save()
    assert (review_dir / FILENAME_USAGE_CACHE).stat().st_mtime_ns == log.stat().st_mtime_ns


def test_save_swallows_replace_error_and_leaves_no_temp(tmp_path, monkeypatch):
    review_dir = tmp_path / "rev"
    log = _log(review_dir)
    cache = UsageCache(review_dir)
    usage = cache.usage([log])

    def boom(*_args, **_kwargs):
        raise OSError("nope")

    monkeypatch.setattr(review.usage_cache.core.serde, "replace_file", boom)
    cache.save()
    assert usage.cost == 0.5
    assert list(review_dir.glob(".usage-cache.json.*.tmp")) == []
    assert not (review_dir / FILENAME_USAGE_CACHE).exists()


def test_paths_helpers_match_with_and_without_cache(tmp_path):
    review_dir = tmp_path / "rev"
    _log(review_dir)
    archive = archives_dir(review_dir)
    archive.mkdir()
    (archive / f"20250101-120000{ARCHIVE_SESSION_EXT}").write_text(RESULT)
    cache = UsageCache(review_dir)
    assert aggregate_session_usage(review_dir, cache) == aggregate_session_usage(review_dir)
    assert archived_session_usage(
        review_dir, "20250101-120000", cache,
    ) == archived_session_usage(review_dir, "20250101-120000")
