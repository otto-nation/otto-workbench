"""`pr board` wiring: schema-version, --prs, and the printed document."""

import json

from conftest import make_ctx

import cli.pr_board
from core.tool_parser import EXIT_USAGE


def test_missing_schema_version_exits_2(capsys):
    code = cli.pr_board.cmd_board(
        ["--checkout", "/tmp", "--phase", "listing"],
        make_ctx(),
    )
    assert code == EXIT_USAGE
    assert "pr board needs --schema-version (serves 1)" in capsys.readouterr().err


def test_prs_with_listing_exits_2(capsys):
    code = cli.pr_board.cmd_board(
        ["--checkout", "/tmp", "--phase", "listing", "--prs", "1,2"],
        make_ctx(),
        schema_version=1,
    )
    assert code == EXIT_USAGE
    assert "--prs" in capsys.readouterr().err


def test_document_printed(monkeypatch, capsys):
    doc = {
        "schema_version": 1,
        "phase": "listing",
        "stamp": "deadbeefdeadbeef",
        "rows": [],
    }
    monkeypatch.setattr("board.document.build", lambda *a, **k: doc)
    code = cli.pr_board.cmd_board(
        ["--checkout", "/x", "--phase", "listing"],
        make_ctx(),
        schema_version=1,
    )
    assert code == 0
    assert json.loads(capsys.readouterr().out) == doc
