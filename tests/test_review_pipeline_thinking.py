import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "claude" / "lib"))

import review_agent


class TestResolveThinkingLevel:
    def test_explicit_wins(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_THINKING", raising=False)
        assert review_agent._resolve_thinking_level("high", "CLAUDE_REVIEW_GROUP_THINKING", "low") == "high"

    def test_phase_env_overrides_default(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_REVIEW_GROUP_THINKING", "medium")
        monkeypatch.delenv("CLAUDE_REVIEW_THINKING", raising=False)
        assert review_agent._resolve_thinking_level(None, "CLAUDE_REVIEW_GROUP_THINKING", "low") == "medium"

    def test_global_env_overrides_default(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_THINKING", raising=False)
        monkeypatch.setenv("CLAUDE_REVIEW_THINKING", "xhigh")
        assert review_agent._resolve_thinking_level(None, "CLAUDE_REVIEW_GROUP_THINKING", "low") == "xhigh"

    def test_default_when_no_env(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_THINKING", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_THINKING", raising=False)
        assert review_agent._resolve_thinking_level(None, "CLAUDE_REVIEW_GROUP_THINKING", "low") == "low"

    def test_phase_env_beats_global(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_REVIEW_GROUP_THINKING", "medium")
        monkeypatch.setenv("CLAUDE_REVIEW_THINKING", "xhigh")
        assert review_agent._resolve_thinking_level(None, "CLAUDE_REVIEW_GROUP_THINKING", "low") == "medium"

    def test_none_explicit_falls_through(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_THINKING", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_THINKING", raising=False)
        assert review_agent._resolve_thinking_level(None, "CLAUDE_REVIEW_GROUP_THINKING", None) is None
