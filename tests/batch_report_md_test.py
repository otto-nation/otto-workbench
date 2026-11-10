"""Finding detail, files and log path in report.md."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.report_md  # noqa: E402


def _doc(findings):
    return {
        "run": {"id": "r", "status": "done"},
        "sessions": [{
            "pr": "o/r#1",
            "state": "s",
            "severity": "block",
            "branch": "b",
            "findings": findings,
            "commands": [],
        }],
    }


def test_a_finding_renders_detail_files_and_log():
    md = batch.report_md.render_markdown(_doc([{
        "reason": "push_refused",
        "detail": "origin moved\n twice",
        "files": ["a.go", "b.go"],
        "log": "/l/x.log",
    }]))
    assert "- push_refused: origin moved twice" in md
    assert "  - files: a.go, b.go" in md
    assert "  - log: /l/x.log" in md
    assert "remedy" not in md


def test_empty_findings_render_none():
    md = batch.report_md.render_markdown(_doc([]))
    assert "findings: (none)" in md
