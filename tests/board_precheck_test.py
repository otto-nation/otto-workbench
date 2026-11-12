"""Offline board --if-changed answers from a stat fingerprint before walking reviews."""

import os

from board.cache import path_for, write, write_viewer
from board.document import Checkout, build, stamp
from board.github import PullList
from board.model import Phase
from batch.store import STATE_FILE, batch_root
import core.workbench_paths


def _co(tmp_path, key="k"):
    return Checkout(str(tmp_path / "co"), "acme/box", "github.com", key)


def _env(tmp_path, monkeypatch, *, checkout=None, listing=None, pulls=None):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    found = checkout or _co(tmp_path)
    listing_rows = list(listing or [])
    monkeypatch.setattr("gh.client.login", lambda: "me")
    monkeypatch.setattr(
        "board.document.review.listing.rows",
        lambda *a, **k: listing_rows,
    )
    monkeypatch.setattr("board.rows.batch.store.busy_prs", lambda ref: frozenset())
    monkeypatch.setattr("board.document._resolve", lambda path: found)
    monkeypatch.setattr(
        "board.github.list_pulls",
        lambda slug: PullList(pulls=list(pulls) if pulls is not None else []),
    )
    return found


def _listing_row():
    return {
        "number": 7,
        "head_sha": "a",
        "title": "T",
        "author": "me",
        "repo": "acme/box",
    }


def _seed_offline(tmp_path, monkeypatch):
    found = _env(tmp_path, monkeypatch)
    write(found.key, Phase.LISTING, {"rows": [_listing_row()]})
    return found


def _bump_mtime(path):
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + 1))


def _count_rows(monkeypatch):
    calls = {"n": 0}

    def counting(*_a, **_k):
        calls["n"] += 1
        return []

    monkeypatch.setattr("board.document.review.listing.rows", counting)
    return calls


def test_offline_if_changed_answers_unchanged_without_walking_reviews(
    tmp_path, monkeypatch,
):
    found = _seed_offline(tmp_path, monkeypatch)
    doc = build(Phase.LISTING, [found.path], offline=True)
    monkeypatch.setattr(
        "board.document.review.listing.rows",
        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("rows must not run")),
    )
    again = build(
        Phase.LISTING, [found.path], if_changed=doc["stamp"], offline=True,
    )
    assert again == {
        "schema_version": 1,
        "phase": "listing",
        "stamp": doc["stamp"],
        "unchanged": True,
    }


def test_a_stamp_from_another_phase_is_not_answered(tmp_path, monkeypatch):
    found = _seed_offline(tmp_path, monkeypatch)
    listing = build(Phase.LISTING, [found.path], offline=True)
    calls = _count_rows(monkeypatch)
    status = build(
        Phase.STATUS, [found.path], if_changed=listing["stamp"], offline=True,
    )
    assert calls["n"] == 1
    assert status.get("unchanged") is not True
    assert status["phase"] == "status"


def test_rewritten_board_cache_invalidates(tmp_path, monkeypatch):
    found = _seed_offline(tmp_path, monkeypatch)
    doc = build(Phase.LISTING, [found.path], offline=True)
    write(found.key, Phase.LISTING, {
        "rows": [
            _listing_row(),
            {
                "number": 8,
                "head_sha": "b",
                "title": "U",
                "author": "you",
                "repo": "acme/box",
            },
        ],
    })
    _bump_mtime(path_for(found.key, Phase.LISTING))
    calls = _count_rows(monkeypatch)
    again = build(
        Phase.LISTING, [found.path], if_changed=doc["stamp"], offline=True,
    )
    assert "unchanged" not in again
    assert "rows" in again
    assert calls["n"] == 1


def test_touched_review_file_invalidates(tmp_path, monkeypatch):
    found = _seed_offline(tmp_path, monkeypatch)
    doc = build(Phase.LISTING, [found.path], offline=True)
    reviews = core.workbench_paths.reviews_dir()
    reviews.mkdir(parents=True, exist_ok=True)
    (reviews / "probe.md").write_text("x")
    calls = _count_rows(monkeypatch)
    again = build(
        Phase.LISTING, [found.path], if_changed=doc["stamp"], offline=True,
    )
    assert calls["n"] == 1


def test_touched_batch_state_invalidates(tmp_path, monkeypatch):
    found = _seed_offline(tmp_path, monkeypatch)
    state = batch_root() / "run1" / STATE_FILE
    state.parent.mkdir(parents=True)
    state.write_text("{}")
    doc = build(Phase.LISTING, [found.path], offline=True)
    _bump_mtime(state)
    calls = _count_rows(monkeypatch)
    again = build(
        Phase.LISTING, [found.path], if_changed=doc["stamp"], offline=True,
    )
    assert calls["n"] == 1


def test_viewer_change_invalidates(tmp_path, monkeypatch):
    found = _seed_offline(tmp_path, monkeypatch)
    write_viewer("me")
    doc = build(Phase.LISTING, [found.path], offline=True)
    write_viewer("you")
    calls = _count_rows(monkeypatch)
    again = build(
        Phase.LISTING, [found.path], if_changed=doc["stamp"], offline=True,
    )
    assert "unchanged" not in again
    assert "rows" in again
    assert calls["n"] == 1


def test_a_change_during_the_walk_is_not_masked(tmp_path, monkeypatch):
    found = _seed_offline(tmp_path, monkeypatch)

    def write_status(*_a, **_k):
        write(found.key, Phase.STATUS, {"rows": [{"number": 7, "head_sha": "a"}]})
        return []

    monkeypatch.setattr("board.document.review.listing.rows", write_status)
    doc = build(Phase.LISTING, [found.path], offline=True)
    calls = _count_rows(monkeypatch)
    again = build(
        Phase.LISTING, [found.path], if_changed=doc["stamp"], offline=True,
    )
    assert calls["n"] == 1


def test_online_listing_never_short_circuits(tmp_path, monkeypatch):
    found = _env(tmp_path, monkeypatch, pulls=[])
    doc = build(Phase.LISTING, [found.path])
    calls = {"n": 0}

    def counting(slug):
        calls["n"] += 1
        return PullList(pulls=[])

    monkeypatch.setattr("board.github.list_pulls", counting)
    again = build(Phase.LISTING, [found.path], if_changed=doc["stamp"])
    assert calls["n"] == 1
    assert again.get("unchanged") is True


def test_offline_stamp_still_hashes_the_document(tmp_path, monkeypatch):
    found = _seed_offline(tmp_path, monkeypatch)
    doc = build(Phase.LISTING, [found.path], offline=True)
    assert stamp(doc) == doc["stamp"]


def test_remember_skips_rewrite_when_memo_current(tmp_path, monkeypatch):
    import board.precheck as pc

    _env(tmp_path, monkeypatch)
    pre = pc.take(["/x"], [], "me", Phase.LISTING)
    pre.remember("s1")
    writes = []
    monkeypatch.setattr("core.serde.write_json", lambda *a, **k: writes.append(a))
    pre.remember("s1")
    assert writes == []
    pre.remember("s2")
    assert len(writes) == 1


def test_memo_key_resolves_checkout_paths(tmp_path, monkeypatch):
    import board.precheck as pc

    _env(tmp_path, monkeypatch)
    a = pc.take([str(tmp_path)], [], "me", Phase.LISTING)
    b = pc.take([str(tmp_path) + "/"], [], "me", Phase.LISTING)
    assert a.path == b.path
