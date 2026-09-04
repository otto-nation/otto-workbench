"""Tests for review_github's pinned metadata fetching."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from gh import pr_reads as rg

PR_PAYLOAD = {
    "title": "feat: something",
    "body": "",
    "headRefName": "feature",
    "baseRefName": "main",
    "headRefOid": "newsha0",
    "additions": 90,
    "deletions": 9,
    "changedFiles": 3,
    "files": [
        {"path": "new.py", "additions": 90, "deletions": 9},
    ],
    "isDraft": False,
    "labels": [],
    "author": {"login": "someone"},
}

PINNED_NUMSTAT = "10\t2\told.py\n5\t0\tshared.py\n"


def _stub_run(monkeypatch, calls: list):
    def fake_pr_view(pr, *fields, repo="", cwd=None):
        calls.append((["gh", "pr", "view", pr, "--repo", repo], cwd))
        return dict(PR_PAYLOAD)

    def fake_git_out(*args, cwd=None, default="", config=None):
        calls.append((["git", *args], cwd))
        if args == ("diff", "--numstat", "origin/main...HEAD"):
            return PINNED_NUMSTAT
        raise AssertionError(f"unexpected git command: {args}")

    monkeypatch.setattr(rg.gh_client, "pr_view", fake_pr_view)
    monkeypatch.setattr(rg.git_client, "out", fake_git_out)


class TestFetchPRMetadataPinned:
    def test_pin_sha_rebuilds_changeset_from_worktree(self, monkeypatch):
        calls: list = []
        _stub_run(monkeypatch, calls)

        pr = rg.fetch_pr_metadata(
            "owner/repo", "1", pin_sha="oldsha0", wt_path="/tmp/pinned",
        )

        assert pr.head_sha == "oldsha0"
        assert pr.files == [
            {"path": "old.py", "additions": 10, "deletions": 2},
            {"path": "shared.py", "additions": 5, "deletions": 0},
        ]
        assert (pr.additions, pr.deletions, pr.changed_files) == (15, 2, 2)
        assert pr.title == "feat: something"

        git_cmd, cwd = calls[-1]
        assert git_cmd == ["git", "diff", "--numstat", "origin/main...HEAD"]
        assert cwd == "/tmp/pinned"

    def test_pin_sha_matching_head_uses_gh_payload(self, monkeypatch):
        calls: list = []
        _stub_run(monkeypatch, calls)

        pr = rg.fetch_pr_metadata(
            "owner/repo", "1", pin_sha="newsha0", wt_path="/tmp/pinned",
        )

        assert pr.head_sha == "newsha0"
        assert [f["path"] for f in pr.files] == ["new.py"]
        assert pr.changed_files == 3
        assert len(calls) == 1

    def test_no_pin_sha_uses_gh_payload(self, monkeypatch):
        calls: list = []
        _stub_run(monkeypatch, calls)

        pr = rg.fetch_pr_metadata("owner/repo", "1")

        assert pr.head_sha == "newsha0"
        assert pr.changed_files == 3
        assert len(calls) == 1
