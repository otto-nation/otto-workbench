"""Shared setup for the board document suites: a fake checkout, gh, and cache root."""

from pathlib import Path

from board.document import Checkout
from board.github import PullList, StatusFetch
from board.model import PullFacts
from conftest import git_in, git_out, seed_repo

ON_BRANCH = "me/fix/x"
OTHER_BRANCH = "other"


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


def seed_checkout(tmp_path):
    """A real git repo as a Checkout, identity via seed_repo's -c user.name=t."""
    repo = seed_repo(tmp_path / "repo")
    return repo, Checkout(str(repo), "acme/box", "github.com", "k")


def add_on_branch_worktree(repo, tmp_path):
    """Linked worktree on ON_BRANCH; returns the path git was given."""
    path = tmp_path / "me-fix-x"
    git_in(repo, "worktree", "add", "-b", ON_BRANCH, str(path))
    return path


def porcelain_worktree(repo, branch: str) -> str:
    """The path `git worktree list --porcelain` spells for *branch*."""
    text = git_out(repo, "worktree", "list", "--porcelain")
    for block in text.split("\n\n"):
        fields = dict(line.partition(" ")[::2] for line in block.splitlines())
        if fields.get("branch") == f"refs/heads/{branch}" and fields.get("worktree"):
            return fields["worktree"]
    raise AssertionError(f"no porcelain worktree for {branch!r} in {text!r}")


def assert_worktree_rows(doc, repo, *, on=7, other=8):
    """*on* carries the porcelain worktree path; *other* is null.

    The row stores `str(Path(porcelain_line))`, which matches porcelain on
    Linux. On macOS `/tmp` and `/var` are `/private/...` after resolve, so a
    spelling mismatch is equated with Path.resolve().
    """
    by_number = {row["number"]: row for row in doc["rows"]}
    porcelain = porcelain_worktree(repo, ON_BRANCH)
    got = by_number[on]["worktree"]
    assert got is not None
    if got != porcelain:
        assert Path(got).resolve() == Path(porcelain).resolve()
    assert by_number[other]["worktree"] is None
