"""The run-session pi extension and the Python it drives agree.

Each test runs the extension's own TypeScript under node and hands the result
to the Python owner: the spellings it copies (verdicts, the run-dir variable,
the session id), the `pr batch note` argv record_decision builds — run through
the real CLI — and the Now block over a real `pr batch report`.

Revert: in ai/pi/extensions/run-session/session.ts noteArgv, pass the reason as
two arguments ("--reason", input.reason); the dash-leading one-word reason below
then fails argparse ("expected one argument") and the note test fails. It must
stay one word: argparse reads a dash-leading string containing a space as a
value even without `=`, so a sentence would not catch the revert.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
EXT_DIR = REPO_ROOT / "ai" / "pi" / "extensions"
RUN_DIR_TS = EXT_DIR / "_shared" / "run-dir.ts"
EVENTS_TS = EXT_DIR / "run-session" / "events.ts"
NOW_TS = EXT_DIR / "run-session" / "now.ts"
SESSION_TS = EXT_DIR / "run-session" / "session.ts"
NODE_TIMEOUT = 60
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.events  # noqa: E402
import batch.run_session  # noqa: E402
import batch.scheduler  # noqa: E402
import batch.store  # noqa: E402
import cli.pr  # noqa: E402
import core.run_events  # noqa: E402
from batch.model import Finding, FindingReason, ItemStatus, Step, Verdict  # noqa: E402
from batch.plan import PlanRow, StepNeed  # noqa: E402
from batch_session_support import seed  # noqa: E402


def _ts(modules, expr, *args):
    """JSON of *expr*, with each of *modules* imported under its name and `args` bound."""
    imports = "".join(f"const {name} = await import({json.dumps(path.as_uri())});"
                      for name, path in modules.items())
    script = (f"{imports}const args = process.argv.slice(1);"
              f"process.stdout.write(JSON.stringify({expr}));")
    out = subprocess.run(["node", "--input-type=module", "-e", script, "--", *args],
                         check=True, capture_output=True, text=True, timeout=NODE_TIMEOUT)
    return json.loads(out.stdout)


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def _seeded():
    rows = [PlanRow("o/r", "/r", 1, "t", "b1", "h", False,
                    {Step.REBASE: StepNeed(True, "x")})]
    run = batch.scheduler.new_run(rows, steps=[Step.REBASE], selected=None, pool=1,
                                  checkouts=["/r"])
    run.items[0].findings.append(Finding(FindingReason.CI_FAILING, "ci", "red on main"))
    run.items[0].status = ItemStatus.NEEDS_SESSION
    return seed(run)


def _history_path(run):
    return batch.store.run_dir(run.id) / core.run_events.EVENTS_FILE


def _note(run, verdict, reason):
    assert _main(["batch", "note", run.id, "o/r#1", "--finding", "ci_failing:ci",
                  "--verdict", verdict, "--reason", reason]) == 0


def test_the_extension_spells_the_python_verdicts():
    assert _ts({"m": SESSION_TS}, "m.VERDICTS") == [v.value for v in Verdict]


def test_the_extension_names_a_run_session_as_the_recipe_does():
    run = _seeded()
    recipe = batch.run_session.recipe(run)
    run_dir = recipe.env[batch.run_session.RUN_DIR_ENV]
    got = _ts({"m": RUN_DIR_TS},
              "[m.RUN_DIR_ENV, m.runSessionDir({ [m.RUN_DIR_ENV]: args[0] }, args[1])]",
              run_dir, recipe.session_id)
    assert got == [batch.run_session.RUN_DIR_ENV, run_dir]


def test_record_decision_argv_is_one_pr_batch_note_accepts(capsys):
    run = _seeded()
    decision = {"pr": "o/r#1", "finding": "ci_failing:ci", "verdict": "handed_off",
                "reason": "-flaky", "commands": ["pr ci --fix", "--weird"]}
    argv = _ts({"m": SESSION_TS}, "m.noteArgv(args[0], JSON.parse(args[1]))",
               run.id, json.dumps(decision))
    assert _main(argv) == 0
    capsys.readouterr()
    [event] = core.run_events.read(batch.store.run_dir(run.id)).events
    assert {k: event[k] for k in ("kind", "item", "finding", "verdict", "reason", "commands")} == {
        "kind": "decision", "item": "o/r#1", "finding": "ci_failing:ci",
        "verdict": "handed_off", "reason": "-flaky", "commands": ["pr ci --fix", "--weird"]}


def test_latest_decisions_and_skipped_lines_agree_with_python(capsys):
    run = _seeded()
    _note(run, "deferred", "first look")
    with _history_path(run).open("a") as fh:
        fh.write("{torn\n")
    _note(run, "fixed", "re-ran green")
    capsys.readouterr()
    history = core.run_events.read(batch.store.run_dir(run.id))
    python = {f"{item}\u0000{finding}": event["verdict"]
              for (item, finding), event in batch.events.latest_decisions(history.events).items()}
    got = _ts({"m": EVENTS_TS},
              "(() => { const c = m.consume(new TextEncoder().encode(args[0]), 0);"
              " return [Object.fromEntries([...m.latestDecisions(c.events)]"
              ".map(([k, v]) => [k, v.verdict])), c.skipped]; })()",
              _history_path(run).read_text())
    assert got == [python, history.skipped]
    assert python == {"o/r#1\u0000ci_failing:ci": "fixed"}


def test_the_now_block_renders_the_real_report_with_its_latest_decision(capsys):
    run = _seeded()
    _note(run, "deferred", "first look")
    _note(run, "fixed", "re-ran green")
    capsys.readouterr()
    assert _main(["batch", "report", run.id, "--offline", "--format", "json"]) == 0
    report = capsys.readouterr().out
    text = _ts({"m": NOW_TS, "e": EVENTS_TS},
               "m.renderNow(args[0], m.parseReport(args[1]).sessions,"
               " e.latestDecisions(e.consume(new TextEncoder().encode(args[2]), 0).events))",
               run.id, report, _history_path(run).read_text())
    assert "### o/r#1 — needs_session" in text
    assert "- finding `ci_failing:ci` — red on main" in text
    assert "  decided: fixed — re-ran green" in text
    assert "first look" not in text
    assert "- run `pr ci --fix --repo-dir /r --branch b1` — fix failing CI" in text
