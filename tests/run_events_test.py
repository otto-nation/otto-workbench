"""`core.run_events`: one JSON line per append, read back skipping what will not parse.

Revert: write the record without its trailing newline in `append`; drop the
`isinstance(obj, dict)` check in `read`.
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import core.run_events  # noqa: E402


def test_append_writes_one_json_line_per_call(tmp_path):
    core.run_events.append(tmp_path, {"kind": "a", "n": 1})
    core.run_events.append(tmp_path, {"kind": "b", "n": 2})
    text = (tmp_path / core.run_events.EVENTS_FILE).read_text()
    assert text == json.dumps({"kind": "a", "n": 1}) + "\n" + json.dumps({"kind": "b", "n": 2}) + "\n"


def test_append_never_creates_the_run_directory(tmp_path):
    gone = tmp_path / "reaped"
    with pytest.raises(FileNotFoundError):
        core.run_events.append(gone, {"kind": "a"})
    assert not gone.exists()


@pytest.mark.parametrize("record", [{}, {"kind": ""}, {"kind": 3}])
def test_append_refuses_a_record_without_a_string_kind(tmp_path, record):
    with pytest.raises(ValueError):
        core.run_events.append(tmp_path, record)
    assert not core.run_events.events_path(tmp_path).exists()


def test_read_of_a_run_with_no_history_is_empty(tmp_path):
    assert core.run_events.read(tmp_path) == core.run_events.History([], 0)


def test_read_skips_torn_and_foreign_lines_and_counts_them(tmp_path):
    lines = [
        json.dumps({"kind": "a"}),
        '{"kind": "torn',
        "[1, 2]",
        json.dumps({"no_kind": 1}),
        "",
        json.dumps({"kind": "from_a_newer_build", "x": 1}),
    ]
    core.run_events.events_path(tmp_path).write_text("\n".join(lines) + "\n")
    history = core.run_events.read(tmp_path)
    assert [e["kind"] for e in history.events] == ["a", "from_a_newer_build"]
    assert history.events[1]["x"] == 1
    assert history.skipped == 3
