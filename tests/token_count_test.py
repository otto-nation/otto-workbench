"""Tests for the exact token counter and the Vertex env it reads."""

from __future__ import annotations

import io
import json
import sys
import time
import urllib.error
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import agent.token_count  # noqa: E402
import agent.vertex_quota  # noqa: E402


def _on_vertex(monkeypatch):
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
    monkeypatch.setenv("CLOUD_ML_REGION", "global")


def _response(payload: dict):
    """A urlopen context manager yielding `payload` as JSON."""
    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    return _Resp(json.dumps(payload).encode())


def _capturing_urlopen(tokens: int = 7):
    """A urlopen stand-in and the dict it records the request body into."""
    captured: dict = {}

    def _capture(req, **kwargs):
        captured["body"] = json.loads(req.data)
        return _response({"input_tokens": tokens})

    return captured, _capture


# ── access_token ───────────────────────────────────────────────────────


class TestAccessToken:
    """The token is resolved once per window, not once per call."""

    def setup_method(self):
        agent.vertex_quota._token_cache = None

    def teardown_method(self):
        agent.vertex_quota._token_cache = None

    def test_reuses_a_token_across_calls(self):
        with patch("agent.vertex_quota._mint_access_token", return_value="tok") as mint:
            assert [agent.vertex_quota.access_token() for _ in range(4)] == ["tok"] * 4
        assert mint.call_count == 1

    def test_mints_again_once_the_window_passes(self):
        with patch("agent.vertex_quota._mint_access_token", side_effect=["a", "b"]):
            assert agent.vertex_quota.access_token() == "a"
            agent.vertex_quota._token_cache = ("a", time.time() - agent.vertex_quota._TOKEN_TTL_SECS - 1)
            assert agent.vertex_quota.access_token() == "b"

    def test_a_failure_is_not_cached(self):
        """A missing credential now must not mean a missing one for five minutes."""
        with patch("agent.vertex_quota._mint_access_token", side_effect=[None, "tok"]):
            assert agent.vertex_quota.access_token() is None
            assert agent.vertex_quota.access_token() == "tok"


# ── vertex_env ───────────────────────────────────────────────────────────────


class TestVertexEnv:
    def test_reads_project_and_region(self, monkeypatch):
        _on_vertex(monkeypatch)
        assert agent.vertex_quota.vertex_env() == ("proj", "global")

    def test_none_when_not_on_vertex(self, monkeypatch):
        _on_vertex(monkeypatch)
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "0")
        assert agent.vertex_quota.vertex_env() is None

    def test_none_when_configuration_is_incomplete(self, monkeypatch):
        """Half-configured is not usable, and must not read as usable."""
        _on_vertex(monkeypatch)
        monkeypatch.delenv("CLOUD_ML_REGION")
        assert agent.vertex_quota.vertex_env() is None


# ── count_tokens ─────────────────────────────────────────────────────────────


class TestCountTokens:
    def test_returns_the_endpoint_count(self, monkeypatch):
        _on_vertex(monkeypatch)
        with patch("agent.token_count.access_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=_response({"input_tokens": 4242})):
            assert agent.token_count.count_tokens("some prompt", "claude-sonnet-5") == 4242

    def test_shorthand_model_is_refused_without_a_round_trip(self, monkeypatch):
        """The endpoint rejects "sonnet" with a 400, so never spend the call."""
        _on_vertex(monkeypatch)
        with patch("urllib.request.urlopen") as urlopen:
            assert agent.token_count.count_tokens("some prompt", "sonnet") is None
        urlopen.assert_not_called()

    def test_none_when_not_on_vertex(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "0")
        with patch("urllib.request.urlopen") as urlopen:
            assert agent.token_count.count_tokens("some prompt", "claude-sonnet-5") is None
        urlopen.assert_not_called()

    def test_none_when_no_credentials(self, monkeypatch):
        _on_vertex(monkeypatch)
        with patch("agent.token_count.access_token", return_value=None), \
             patch("urllib.request.urlopen") as urlopen:
            assert agent.token_count.count_tokens("some prompt", "claude-sonnet-5") is None
        urlopen.assert_not_called()

    def test_transport_failure_is_none_not_an_exception(self, monkeypatch):
        """An uncounted prompt is a missing measurement, never a failed review."""
        _on_vertex(monkeypatch)
        with patch("agent.token_count.access_token", return_value="tok"), \
             patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            assert agent.token_count.count_tokens("some prompt", "claude-sonnet-5") is None

    def test_malformed_response_is_none(self, monkeypatch):
        _on_vertex(monkeypatch)
        with patch("agent.token_count.access_token", return_value="tok"), \
             patch("urllib.request.urlopen", return_value=_response({"unexpected": 1})):
            assert agent.token_count.count_tokens("some prompt", "claude-sonnet-5") is None

    def test_system_and_tools_are_counted_when_given(self, monkeypatch):
        """The overhead a prompt is charged beyond its own text is measurable."""
        _on_vertex(monkeypatch)
        captured, side_effect = _capturing_urlopen()
        with patch("agent.token_count.access_token", return_value="tok"), \
             patch("urllib.request.urlopen", side_effect=side_effect):
            agent.token_count.count_tokens(
                "text", "claude-sonnet-5",
                system="You review code.",
                tools=[{"name": "Read", "description": "", "input_schema": {}}],
            )
        assert captured["body"]["system"] == "You review code."
        assert captured["body"]["tools"][0]["name"] == "Read"
        assert captured["body"]["model"] == "claude-sonnet-5"

    def test_omits_system_and_tools_when_absent(self, monkeypatch):
        """An empty system prompt is not the same as one worth counting."""
        _on_vertex(monkeypatch)
        captured, side_effect = _capturing_urlopen()
        with patch("agent.token_count.access_token", return_value="tok"), \
             patch("urllib.request.urlopen", side_effect=side_effect):
            agent.token_count.count_tokens("text", "claude-sonnet-5")
        assert "system" not in captured["body"]
        assert "tools" not in captured["body"]

    def test_regional_and_global_hosts_differ(self, monkeypatch):
        """The global endpoint has no region prefix; a regional one does."""
        assert agent.token_count._endpoint("proj", "global").startswith(
            "https://aiplatform.googleapis.com/"
        )
        assert agent.token_count._endpoint("proj", "us-east5").startswith(
            "https://us-east5-aiplatform.googleapis.com/"
        )

    def test_multi_region_hosts_use_the_rep_frontend(self):
        """`us` and `eu` are not `{region}-aiplatform` — that host 404s."""
        assert agent.token_count._endpoint("proj", "us").startswith(
            "https://aiplatform.us.rep.googleapis.com/"
        )
        assert agent.token_count._endpoint("proj", "eu").startswith(
            "https://aiplatform.eu.rep.googleapis.com/"
        )
        assert "locations/us" in agent.token_count._endpoint("proj", "us")

    def test_http_error_names_the_status_and_does_not_claim_an_estimate(
        self, monkeypatch, capsys,
    ):
        """A 404 is a 404, not a URLError, and nothing estimated anything."""
        _on_vertex(monkeypatch)
        err = urllib.error.HTTPError(
            "https://example.invalid", 404, "Not Found",
            hdrs=None, fp=io.BytesIO(b"nope"),
        )
        with patch("agent.token_count.access_token", return_value="tok"), \
             patch("urllib.request.urlopen", side_effect=err):
            assert agent.token_count.count_tokens(
                "some prompt", "claude-sonnet-5",
            ) is None
        err_text = capsys.readouterr().err
        assert "HTTP 404" in err_text
        assert "using estimate" not in err_text

    def test_transport_failure_does_not_claim_an_estimate(
        self, monkeypatch, capsys,
    ):
        _on_vertex(monkeypatch)
        with patch("agent.token_count.access_token", return_value="tok"), \
             patch("urllib.request.urlopen", side_effect=urllib.error.URLError("down")):
            assert agent.token_count.count_tokens(
                "some prompt", "claude-sonnet-5",
            ) is None
        err_text = capsys.readouterr().err
        assert "using estimate" not in err_text
        assert "count skipped" in err_text
