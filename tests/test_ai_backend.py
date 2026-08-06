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


class TestAgentInvocation:
    def test_all_backends_accept_the_same_object(self):
        """One object, three modules — a reordered field cannot misbind."""
        import inspect

        import ai_backend
        import ai_backend_claude
        import ai_backend_pi

        for mod in (ai_backend, ai_backend_claude, ai_backend_pi):
            for fn_name in ("invoke_agent", "invoke_fix"):
                params = list(inspect.signature(getattr(mod, fn_name)).parameters)
                assert params == ["inv"], f"{mod.__name__}.{fn_name} takes {params}"

    def test_defaults_leave_every_optional_field_unset(self):
        import ai_backend

        inv = ai_backend.AgentInvocation(prompt="hi")
        assert inv.session_log == ""
        assert inv.add_dirs == []
        assert inv.agent is None
        assert inv.max_turns is None
        assert inv.max_budget is None
        assert inv.model == ""
        assert inv.thinking is None
        assert inv.provider is None
        assert inv.label == ""

    def test_is_frozen(self):
        import dataclasses

        import ai_backend
        import pytest

        inv = ai_backend.AgentInvocation(prompt="hi")
        with pytest.raises(dataclasses.FrozenInstanceError):
            inv.prompt = "bye"

    def test_add_dirs_are_not_shared_between_instances(self):
        import ai_backend

        a = ai_backend.AgentInvocation(prompt="a")
        b = ai_backend.AgentInvocation(prompt="b")
        a.add_dirs.append("/tmp")
        assert b.add_dirs == []


class TestBuildAddDirs:
    def test_review_dir_appended_when_distinct(self):
        import review_agent

        dirs = review_agent.build_add_dirs(
            "/wt", "/reviews", "/elsewhere/review.md",
        )
        assert dirs == ["/reviews", "/wt", "/elsewhere"]

    def test_review_dir_omitted_when_already_covered(self):
        import review_agent

        dirs = review_agent.build_add_dirs("/wt", "/reviews", "/reviews/review.md")
        assert dirs == ["/reviews", "/wt"]

    def test_no_review_file(self):
        import review_agent

        assert review_agent.build_add_dirs("/wt", "/reviews") == ["/reviews", "/wt"]
