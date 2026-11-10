"""Tests for gh.repo_settings — the merge buttons GitHub shows for a repo."""

import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import gh.repo_settings  # noqa: E402


def _methods(**fields):
    data = {
        "squashMergeAllowed": True,
        "mergeCommitAllowed": False,
        "rebaseMergeAllowed": False,
    }
    data.update(fields)
    return data


def test_squash_only_repo():
    with patch("gh.client.json_out", return_value=_methods()) as view:
        methods = gh.repo_settings.merge_methods("acme/widget", cwd="/wt")
    view.assert_called_once()
    assert methods is not None
    assert methods.squash_only
    assert methods.squash and not methods.merge and not methods.rebase


def test_any_missing_field_is_unreadable():
    for field in gh.repo_settings.MERGE_FIELDS:
        data = _methods()
        data.pop(field)
        with patch("gh.client.json_out", return_value=data):
            assert gh.repo_settings.merge_methods("acme/widget", cwd="/wt") is None


def test_failed_call_is_unreadable():
    with patch("gh.client.json_out", return_value=None):
        assert gh.repo_settings.merge_methods("acme/widget", cwd="/wt") is None


def test_repo_slug_is_positional_not_flag():
    with patch("gh.client.json_out", return_value=_methods()) as view:
        gh.repo_settings.merge_methods("acme/widget", cwd="/wt")
    args, kwargs = view.call_args
    assert args[:2] == ("repo", "view")
    assert args[2] == "acme/widget"
    assert "--repo" not in args
    assert kwargs["cwd"] == "/wt"
    assert kwargs["default"] is None

    with patch("gh.client.json_out", return_value=_methods()) as view:
        gh.repo_settings.merge_methods("github.example.com/acme/widget", cwd="/wt")
    assert view.call_args[0][2] == "github.example.com/acme/widget"
