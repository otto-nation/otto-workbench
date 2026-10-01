"""Tests for ai/lib/core/memory.py — repo identity and topic-file scanning."""

from __future__ import annotations

import hashlib
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
sys.path.insert(0, str(LIB_DIR))

import core.memory
import core.sessions  # noqa: E402

# Three paths whose canonical_slug is identical. A key that is only the slug
# silently merges three repos into one memory directory.
COLLIDING_PATHS = (
    "/Users/x/a-b/c",
    "/Users/x/a/b/c",
    "/Users/x/a.b/c",
)


def test_canonical_slug_collides_on_hyphen_slash_and_dot():
    slugs = {core.sessions.canonical_slug(path) for path in COLLIDING_PATHS}
    assert slugs == {"--Users-x-a-b-c--"}


def test_repo_key_distinguishes_canonical_slug_collisions(monkeypatch):
    monkeypatch.setattr(core.memory, "shared_dir", lambda directory: directory)
    keys = [core.memory.repo_key(path) for path in COLLIDING_PATHS]
    assert len(set(keys)) == 3
    for path, key in zip(COLLIDING_PATHS, keys):
        slug = core.sessions.canonical_slug(path)[:64]
        digest = hashlib.sha256(path.encode()).hexdigest()[:8]
        assert key == f"{slug}-{digest}"


def test_repo_key_raises_when_git_cannot_answer(monkeypatch):
    monkeypatch.setattr(core.memory, "shared_dir", lambda directory: None)
    with pytest.raises(ValueError, match="no shared git dir"):
        core.memory.repo_key("/Users/x/a-b/c")


def test_scan_topic_file_returns_none_for_a_vanished_file(tmp_path):
    missing = tmp_path / "gone.md"
    assert core.memory.scan_topic_file(missing, datetime.now()) is None


def test_scan_topic_file_stats_once(tmp_path, monkeypatch):
    topic = tmp_path / "feedback.md"
    topic.write_text("---\nname: Feedback\n---\n\nbody text\n")
    real_stat = Path.stat
    calls: list[Path] = []

    def spy(self, *args, **kwargs):
        calls.append(Path(self))
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", spy)
    now = datetime.now()
    result = core.memory.scan_topic_file(topic, now)
    assert result is not None
    assert result.body is None
    assert [c for c in calls if c == topic] == [topic]

    calls.clear()
    with_body = core.memory.scan_topic_file(topic, now, with_body=True)
    assert with_body is not None
    assert with_body.body == "body text"
    assert [c for c in calls if c == topic] == [topic]


def test_scan_topic_file_reads_frontmatter(tmp_path):
    topic = tmp_path / "feedback.md"
    topic.write_text(
        "---\nname: Feedback\ndescription: notes\ntype: feedback\n---\n\nhello\n"
    )
    now = datetime.fromtimestamp(topic.stat().st_mtime)
    row = core.memory.scan_topic_file(topic, now)
    assert row == core.memory.TopicFile(
        filename="feedback.md",
        name="Feedback",
        description="notes",
        type="feedback",
        modified=now.strftime(core.memory.DATE_FMT),
        stale=False,
        age_days=0,
        body=None,
    )


def test_scan_topic_file_marks_stale_after_threshold(tmp_path):
    topic = tmp_path / "old.md"
    topic.write_text("---\nname: Old\n---\n")
    mtime = datetime.fromtimestamp(topic.stat().st_mtime)
    now = mtime + timedelta(days=core.memory.STALE_DAYS + 1)
    row = core.memory.scan_topic_file(topic, now)
    assert row is not None
    assert row.stale is True
    assert row.age_days == core.memory.STALE_DAYS + 1
