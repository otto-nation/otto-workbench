"""Tests for fix.verify — the gate that checks claimed fixes.

The engine's contract with the gate is tested in fix_engine_gate_test. What is
held here is the runner's own batching: a gate handed more claims than its cap
covers used to spend ~1 turn an item, which is how 39 claimed fixes against a
40-turn budget never reached most of them.
"""

import json
import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import agent.invoke
import agent.phases
from core.phases import Phase
import fix.verify
from fix.types import FixItem


class _Adapter:
    def __init__(self, wt_path):
        self.workdir = Path(wt_path)
        self.artifacts = self.workdir / "artifacts"
        self.artifacts.mkdir()
        self.branch = "isaac/feat/x"
        self.repo = "owner/repo"
        self.pr = ""
        self.config = None
        self.effort = None
        self.model = ""

    def add_dirs(self):
        return [self.workdir]

    def verify_tracking_path(self, chunk: int):
        return self.artifacts / f"verify-tracking-{chunk}.md"

    def verify_session_log(self, chunk: int):
        return self.artifacts / f"verify-session-{chunk}.jsonl"


def _items(count):
    return [
        FixItem(id=f"i{n}", file="a.py", line=n + 1, label=f"item {n}")
        for n in range(count)
    ]


def test_work_over_the_chunk_size_is_split(tmp_path):
    """One invoke holding every claim is what starved the gate of turns."""
    adapter = _Adapter(tmp_path)
    chunk = agent.phases.phase_chunk_size(Phase.FIX_VERIFY)
    calls = []

    def run_fix(_phase, _prompt, **kwargs):
        calls.append(kwargs["max_turns"])
        return agent.invoke.FixResult(0, None)

    with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
        fix.verify.run(
            Phase.FIX_VERIFY, "", items=_items(chunk + 1), adapter=adapter,
        )

    assert len(calls) == 2
    assert calls[0] == agent.phases.phase_turns(Phase.FIX_VERIFY, items=chunk)
    assert calls[1] == agent.phases.phase_turns(Phase.FIX_VERIFY, items=1)


def test_a_single_chunk_is_not_numbered(tmp_path):
    adapter = _Adapter(tmp_path)
    labels = []

    def run_fix(_phase, _prompt, **kwargs):
        labels.append(kwargs["label"])
        return agent.invoke.FixResult(0, None)

    with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
        fix.verify.run(
            Phase.FIX_VERIFY, "", items=_items(1), adapter=adapter,
        )

    assert labels == ["Verify gate"]


def test_each_chunk_writes_its_own_tracking_file(tmp_path):
    adapter = _Adapter(tmp_path)
    chunk = agent.phases.phase_chunk_size(Phase.FIX_VERIFY)
    seen = []
    # Captured while each chunk is in flight. Checking existence after the run
    # would pass on two files written by one chunk; the point is that chunk 2
    # is answering on its own checklist rather than reading chunk 1's, and only
    # a read taken during the call can tell those apart.
    tracking = []

    def run_fix(_phase, _prompt, **kwargs):
        seen.append(kwargs["session_log"])
        tracking.append(sorted(
            p.name for p in adapter.artifacts.glob("verify-tracking-*.md")
        ))
        return agent.invoke.FixResult(0, None)

    with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
        fix.verify.run(
            Phase.FIX_VERIFY, "", items=_items(chunk + 1), adapter=adapter,
        )

    assert len(seen) == 2
    assert seen[0] != seen[1]
    assert seen[0].endswith("verify-session-1.jsonl")
    assert seen[1].endswith("verify-session-2.jsonl")
    assert tracking == [
        ["verify-tracking-1.md"],
        ["verify-tracking-1.md", "verify-tracking-2.md"],
    ]
    assert not (adapter.artifacts / "verify-tracking.md").exists()


def test_a_single_chunk_still_uses_the_suffixed_name(tmp_path):
    adapter = _Adapter(tmp_path)

    def run_fix(_phase, _prompt, **kwargs):
        assert kwargs["session_log"].endswith("verify-session-1.jsonl")
        return agent.invoke.FixResult(0, None)

    with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
        fix.verify.run(
            Phase.FIX_VERIFY, "", items=_items(1), adapter=adapter,
        )

    assert (adapter.artifacts / "verify-tracking-1.md").exists()
    assert not (adapter.artifacts / "verify-tracking.md").exists()


def _tool_end(text: str) -> str:
    return json.dumps({
        "type": "tool_execution_end", "toolCallId": "c1", "toolName": "edit",
        "result": {"content": [{"type": "text", "text": text}], "details": {}},
        "isError": True,
    })


def test_a_malformed_checklist_write_retries_unanswered_items(tmp_path):
    adapter = _Adapter(tmp_path)
    calls = []

    def run_fix(_phase, prompt, **kwargs):
        tracking = adapter.verify_tracking_path(1)
        calls.append({
            "log": kwargs["session_log"],
            "prompt": prompt,
            "text": tracking.read_text(),
        })
        if "retry" not in kwargs["session_log"]:
            tracking.write_text(tracking.read_text().replace(
                "- [ ] verified", "- [x] verified", 1))
            Path(kwargs["session_log"]).write_text(
                _tool_end(
                    f'Invalid arguments for tool "edit": Expected \',\' {tracking}'
                ) + "\n")
        else:
            tracking.write_text(tracking.read_text().replace(
                "- [ ] verified", "- [x] verified", 1))
        return agent.invoke.FixResult(0, None)

    with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
        verdicts = fix.verify.run(
            Phase.FIX_VERIFY, "", items=_items(2), adapter=adapter,
        )

    assert len(calls) == 2
    assert calls[1]["log"].endswith("verify-session-1-retry.jsonl")
    assert "Rewrite the whole file" in calls[1]["prompt"]
    assert "file-write tool" in calls[1]["prompt"]
    assert "<!-- fix:i0 -->" not in calls[1]["text"]
    assert "<!-- fix:i1 -->" in calls[1]["text"]
    assert verdicts["i0"].ok is True
    assert verdicts["i1"].ok is True


def test_a_broken_tick_with_failed_writes_is_unrecorded(tmp_path):
    adapter = _Adapter(tmp_path)

    def run_fix(_phase, _prompt, **kwargs):
        tracking = adapter.verify_tracking_path(1)
        Path(kwargs["session_log"]).write_text(
            _tool_end(
                f'Invalid arguments for tool "edit": Expected \',\' {tracking}'
            ) + "\n")
        if "retry" in kwargs["session_log"]:
            tracking.write_text(tracking.read_text().replace(
                "- [ ] broken", "- [x] broken", 1))
        return agent.invoke.FixResult(0, None)

    with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
        verdicts = fix.verify.run(
            Phase.FIX_VERIFY, "", items=_items(1), adapter=adapter,
        )

    assert verdicts["i0"].ok is None
    assert verdicts["i0"].recorded is False


def test_a_broken_tick_survives_when_a_later_write_filled_the_checklist(tmp_path):
    adapter = _Adapter(tmp_path)

    def run_fix(_phase, _prompt, **kwargs):
        tracking = adapter.verify_tracking_path(1)
        Path(kwargs["session_log"]).write_text(
            _tool_end(
                f'Invalid arguments for tool "edit": Expected \',\' {tracking}'
            ) + "\n")
        tracking.write_text(tracking.read_text().replace(
            "- [ ] broken", "- [x] broken"))
        return agent.invoke.FixResult(0, None)

    with patch.object(agent.invoke, "run_fix", side_effect=run_fix):
        verdicts = fix.verify.run(
            Phase.FIX_VERIFY, "", items=_items(1), adapter=adapter,
        )

    assert verdicts["i0"].ok is False
