"""A one-shot `pr batch run` emits `run_started`.

Revert: stop emitting run_started from `_emit_started`.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.model  # noqa: E402
import batch.orchestrate  # noqa: E402
import batch.plan  # noqa: E402
import cli.pr  # noqa: E402
from batch.plan import Plan, PlanRow, StepNeed  # noqa: E402

BIN_DIR = REPO_ROOT / "ai" / "bin"


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def test_run_emits_run_started(monkeypatch, capsys):
    row = PlanRow("o/r", "/r", 1, "t", "b", "h", False,
                  {batch.model.Step.REBASE: StepNeed(True, "x")})
    monkeypatch.setattr(batch.plan, "build_run_plan", lambda dirs, prs=None: Plan("me", [row]))
    monkeypatch.setattr(batch.orchestrate, "submit_ready", lambda *a, **k: 0)
    assert _main(["batch", "run", "--checkout", "/r", "--steps", "rebase"]) == 0
    kinds = [json.loads(line)["kind"] for line in capsys.readouterr().out.splitlines()
             if line.startswith("{")]
    assert "run_started" in kinds
