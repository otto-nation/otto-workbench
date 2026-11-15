"""The run-session extension reads a real `pr target report` and target history unchanged.

Runs the extension's own TypeScript under node (as pi_run_session_contract_test
does): parseReport over the real report, renderNow with the latest
`pr target note` decision, and historyText over the target's step events.

Revert: in batch.target_report.entry, set state="done"; the "1 of 1 PRs
waiting" assertion fails.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
EXT_DIR = REPO_ROOT / "ai" / "pi" / "extensions" / "run-session"
NOW_TS = EXT_DIR / "now.ts"
EVENTS_TS = EXT_DIR / "events.ts"
NODE_TIMEOUT = 60
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.pr  # noqa: E402
import core.run_events  # noqa: E402
import pr.target_events  # noqa: E402
from pr_target_support import make_target  # noqa: E402


def _ts(modules, expr, *args):
    imports = "".join(f"const {name} = await import({json.dumps(path.as_uri())});"
                      for name, path in modules.items())
    # The arguments travel as JSON on stdin, not argv: a report is over a kilobyte, and an
    # argument that long is more than some hosts let a node process start with.
    script = (f"{imports}import {{ readFileSync }} from 'node:fs';"
              f"const args = JSON.parse(readFileSync(0, 'utf8'));"
              f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run(["node", "--input-type=module", "-e", script], input=json.dumps(list(args)),
                         check=True, capture_output=True, text=True, timeout=NODE_TIMEOUT)
    return json.loads(out.stdout)


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def test_the_now_block_renders_the_real_target_report_with_its_latest_note(tmp_path, capsys):
    t = make_target(tmp_path, jobs=["lint"])
    for verdict, reason in (("deferred", "first look"), ("fixed", "re-ran green")):
        assert _main(["--repo-dir", str(t.repo), "target", "note", "o/r#7", "--finding",
                      "ci_failing:ci", "--verdict", verdict, "--reason", reason]) == 0
    capsys.readouterr()
    assert _main(["--repo-dir", str(t.repo), "target", "report"]) == 0
    report = capsys.readouterr().out
    history = core.run_events.events_path(t.target_dir).read_text()
    parsed = _ts({"m": NOW_TS}, "m.parseReport(args[0])", report)
    assert parsed["ok"] is True and len(parsed["sessions"]) == 1
    text = _ts({"m": NOW_TS, "e": EVENTS_TS},
               "m.renderNow('t', m.parseReport(args[0]).sessions,"
               " e.latestDecisions(e.consume(new TextEncoder().encode(args[1]), 0).events))",
               report, history)
    assert "1 of 1 PRs waiting on you" in text
    assert "### o/r#7 — needs_session (act) · feat/x" in text
    assert "- finding `ci_failing:ci` — failing at " in text
    assert "  decided: fixed — re-ran green" in text
    assert "- run `pr ci --fix --repo-dir " in text


def test_target_step_events_fold_into_one_history_line(tmp_path):
    t = make_target(tmp_path)
    history = pr.target_events.StepHistory(t.target_dir, "inv-1", "o/r#7", "comments")
    history.started()
    history.finished(0)
    text = _ts({"e": EVENTS_TS},
               "e.historyText(e.consume(new TextEncoder().encode(args[0]), 0).events, 0, 'H')",
               core.run_events.events_path(t.target_dir).read_text())
    assert text.splitlines()[0] == "H (2 events)"
    assert "o/r#7" in text.splitlines()
    assert any(line.endswith("comments done (exit 0)") for line in text.splitlines())
    assert not any("comments started" in line for line in text.splitlines())
