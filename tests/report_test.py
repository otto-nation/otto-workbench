"""How a tool's report reaches stdout — one object, or a stream a reader can split."""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from core import report  # noqa: E402


def test_emit_json_writes_one_parseable_object(capsys):
    report.emit_json({"conclusion": "failure", "failures": []})
    out = capsys.readouterr().out
    assert json.loads(out) == {"conclusion": "failure", "failures": []}


def test_emit_json_indents_so_a_person_can_read_it(capsys):
    report.emit_json({"a": {"b": 1}})
    assert '\n  "a"' in capsys.readouterr().out


def test_emit_stream_json_tags_the_report_with_its_type(capsys):
    report.emit_stream_json({"failures": []}, "partial")
    _, _, payload = capsys.readouterr().out.partition("---\n")
    assert json.loads(payload) == {"failures": [], "type": "partial"}


def test_emit_stream_json_delimits_each_report(capsys):
    report.emit_stream_json({"n": 1}, "partial")
    report.emit_stream_json({"n": 2}, "final")
    chunks = [c for c in capsys.readouterr().out.split("---\n") if c.strip()]
    assert [json.loads(c)["type"] for c in chunks] == ["partial", "final"]
    assert [json.loads(c)["n"] for c in chunks] == [1, 2]


def test_emit_stream_json_does_not_let_a_report_overwrite_its_type(capsys):
    """The tag is the stream's, not the report's — a `type` key of its own loses."""
    report.emit_stream_json({"type": "whatever"}, "final")
    _, _, payload = capsys.readouterr().out.partition("---\n")
    assert json.loads(payload)["type"] == "final"
