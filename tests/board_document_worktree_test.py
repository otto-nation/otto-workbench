"""Board document listing and status --prs rows carry live worktree paths."""

from board.document import build
from board.model import Phase
from board_document_support import (
    OTHER_BRANCH,
    ON_BRANCH,
    add_on_branch_worktree,
    assert_worktree_rows,
    board_env,
    pull_facts,
    seed_checkout,
)


def _two_pulls():
    return [
        pull_facts(7, "aaa", branch=ON_BRANCH),
        pull_facts(8, "bbb", title="U", author="you", branch=OTHER_BRANCH),
    ]


def test_listing_rows_carry_worktree(tmp_path, monkeypatch):
    repo, checkout = seed_checkout(tmp_path)
    add_on_branch_worktree(repo, tmp_path)
    found = board_env(tmp_path, monkeypatch, checkout=checkout, pulls=_two_pulls())
    doc = build(Phase.LISTING, [found.path])
    assert_worktree_rows(doc, repo)


def test_status_prs_rows_carry_worktree(tmp_path, monkeypatch):
    repo, checkout = seed_checkout(tmp_path)
    add_on_branch_worktree(repo, tmp_path)
    found = board_env(tmp_path, monkeypatch, checkout=checkout, pulls=_two_pulls())
    doc = build(Phase.STATUS, [found.path], prs=[7, 8])
    assert doc["complete"] is False
    assert_worktree_rows(doc, repo)
