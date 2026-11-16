"""Shared setup for the board document suites: a fake checkout, gh, and cache root."""

from board.document import Checkout
from board.github import PullList, StatusFetch
from board.model import PullFacts


def fake_checkout(tmp_path, key="k"):
    return Checkout(str(tmp_path / "co"), "acme/box", "github.com", key)


def board_env(tmp_path, monkeypatch, *, checkout=None, pulls=None, status=None, owed_plan=None, listing=None, real_listing=False):
    monkeypatch.setenv("WORKBENCH_CACHE_DIR", str(tmp_path))
    found = checkout or fake_checkout(tmp_path)
    listing_rows = list(listing or [])
    monkeypatch.setattr("gh.client.login", lambda: "me")
    if not real_listing:
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

    def fake_fetch(slug, numbers, *, executor=None):
        if status is not None:
            return status
        return StatusFetch(by_number={}, partial=False)

    monkeypatch.setattr("board.github.fetch_status", fake_fetch)
    if owed_plan is not None:
        monkeypatch.setattr(
            "board.document.batch.plan.build_plan",
            lambda checkouts, steps=None: owed_plan,
        )
    return found


def pull_facts(number, head_sha, *, title="T", author="me", **kwargs):
    return PullFacts(
        repo="acme/box",
        number=number,
        title=title,
        author=author,
        head_sha=head_sha,
        **kwargs,
    )
