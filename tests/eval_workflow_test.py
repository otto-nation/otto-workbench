"""The weekly eval workflow must pass --models on every eval-models invocation."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "eval.yml"


def _logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    buf = ""
    for line in text.splitlines():
        stripped = line.rstrip()
        if stripped.endswith("\\"):
            buf += stripped[:-1] + " "
            continue
        buf += stripped
        lines.append(buf)
        buf = ""
    if buf:
        lines.append(buf)
    return lines


def test_every_eval_models_invocation_passes_models():
    text = EVAL_WORKFLOW.read_text()
    invocations = [
        line for line in _logical_lines(text) if "ai/bin/eval-models" in line
    ]
    assert invocations, f"{EVAL_WORKFLOW} should invoke eval-models"
    missing = [line for line in invocations if "--models" not in line]
    assert missing == []
