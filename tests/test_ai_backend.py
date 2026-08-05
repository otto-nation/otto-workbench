import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "ai" / "lib"))

import ai_backend


class TestBackendSelection:
    def test_defaults_to_claude(self, monkeypatch):
        monkeypatch.delenv("AI_BACKEND", raising=False)
        assert ai_backend._backend() is ai_backend.Backend.CLAUDE

    def test_reads_env(self, monkeypatch):
        monkeypatch.setenv("AI_BACKEND", "pi")
        assert ai_backend._backend() is ai_backend.Backend.PI

    def test_unrecognised_backend_falls_back_to_claude(self, monkeypatch):
        monkeypatch.setenv("AI_BACKEND", "not-a-backend")
        assert ai_backend._backend() is ai_backend.Backend.CLAUDE


class TestPreflightDispatch:
    def test_routes_to_claude_backend(self, monkeypatch):
        monkeypatch.setenv("AI_BACKEND", "claude")
        import ai_backend_claude
        monkeypatch.setattr(ai_backend_claude, "preflight", lambda models, trail: False)
        assert ai_backend.preflight({"claude-sonnet-5": ["group"]}, MagicMock()) is False

    def test_pi_backend_skips_vertex_quota(self, monkeypatch):
        """Regression: Vertex env left exported must not abort a Pi run."""
        monkeypatch.setenv("AI_BACKEND", "pi")
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
        monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")

        import vertex_quota

        def _unreachable(*args, **kwargs):
            pytest.fail("Pi run reached the Vertex quota API")

        monkeypatch.setattr(vertex_quota, "check_quota", _unreachable)
        assert ai_backend.preflight({"claude-sonnet-5": ["group"]}, MagicMock()) is True
