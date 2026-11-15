"""`pr target report`: the target's report as JSON, read offline at LOCAL depth.

Revert: in cmd_target, print {"schema_version": 1, "sessions": []} instead of
batch.target_report.report(...); the report test fails.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import cli.schema  # noqa: E402
import pr.context  # noqa: E402
from pr.fix import FixOutcome, ItemOutcome  # noqa: E402
from pr_target_support import make_target  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def test_report_prints_the_targets_entry_without_asking_github(tmp_path, capsys):
    t = make_target(tmp_path, items=[ItemOutcome(id="PRRT_1", outcome=FixOutcome.NEEDS_HUMAN)],
                    jobs=["lint"])
    with patch.object(pr.context, "resolve", side_effect=AssertionError("asked gh")):
        assert _main(["--repo-dir", str(t.repo), "target", "report"]) == 0
    doc = json.loads(capsys.readouterr().out)
    [entry] = doc["sessions"]
    assert doc["schema_version"] == 1
    assert entry["pr"] == "o/r#7"
    assert [f"{f['reason']}:{f['step']}" for f in entry["findings"]] == [
        "threads_need_human:comments", "ci_failing:ci"]
    assert Path(entry["worktree"]).resolve() == t.repo.resolve()
    assert entry["commands"][0]["argv"][:3] == ["pr", "comments", "--fix"]


def test_a_target_with_nothing_waiting_reports_no_sessions(tmp_path, capsys):
    t = make_target(tmp_path)
    assert _main(["--repo-dir", str(t.repo), "target", "report"]) == 0
    assert json.loads(capsys.readouterr().out) == {"schema_version": 1, "sessions": []}


def test_report_serves_schema_version_1():
    assert cli.schema.served_schema_versions("target", ["report"]) == (1,)
