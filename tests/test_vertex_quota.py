"""Tests for vertex_quota module."""

from __future__ import annotations

import json
import stat
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import urllib.error

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import vertex_quota as vq


# ── Fixtures ─────────────────────────────────────────────────────────────────


def _quota_response(buckets: list[dict]) -> dict:
    return {"consumerQuotaLimits": [{"quotaBuckets": buckets}]}


def _regional_bucket(region: str, model: str, limit: str | None) -> dict:
    return {
        "dimensions": {"region": region, "base_model": model},
        "effectiveLimit": limit,
    }


def _global_bucket(model: str, limit: str | None) -> dict:
    return {
        "dimensions": {"base_model": model},
        "effectiveLimit": limit,
    }


SAMPLE_REGIONAL_RESPONSE = _quota_response([
    _regional_bucket("us-east5", "anthropic-claude-sonnet-4-5", "2000000"),
    _regional_bucket("us-east5", "anthropic-claude-sonnet-4-6", "2000000"),
    _regional_bucket("us-east5", "anthropic-claude-opus-4-6", "4000000"),
    _regional_bucket("us-east5", "anthropic-claude-haiku-4-5", "2000000"),
    _regional_bucket("us-central1", "anthropic-claude-sonnet-4-5", "1000000"),
])

SAMPLE_GLOBAL_RESPONSE = _quota_response([
    _global_bucket("anthropic-claude-sonnet-4-5", "2000000"),
    _global_bucket("anthropic-claude-sonnet-4-6", "2000000"),
    _global_bucket("anthropic-claude-opus-4-6", "8000000"),
    _global_bucket("anthropic-claude-haiku-4-5", "5000000"),
])


def _vertex_env(monkeypatch) -> None:
    monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
    monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
    monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")


# ── is_checkable ─────────────────────────────────────────────────────────────


class TestIsCheckable:
    def test_concrete_model_id(self):
        assert vq.is_checkable("claude-sonnet-5")

    def test_versioned_model_id(self):
        assert vq.is_checkable("claude-haiku-4-5@20251001")

    def test_already_prefixed(self):
        assert vq.is_checkable("anthropic-claude-sonnet-5")

    def test_cli_shorthand_not_checkable(self):
        assert not vq.is_checkable("sonnet")
        assert not vq.is_checkable("opus")
        assert not vq.is_checkable("haiku")


# ── resolve_vertex_model_id ──────────────────────────────────────────────────


class TestResolveVertexModelId:
    def test_full_model_name(self):
        assert vq.resolve_vertex_model_id("claude-sonnet-4-6") == "anthropic-claude-sonnet-4-6"

    def test_already_prefixed(self):
        assert vq.resolve_vertex_model_id("anthropic-claude-sonnet-4-6") == "anthropic-claude-sonnet-4-6"

    def test_version_suffix_stripped(self):
        assert vq.resolve_vertex_model_id("claude-haiku-4-5@20251001") == "anthropic-claude-haiku-4-5"


# ── _fetch_provisioned_models ────────────────────────────────────────────────


class TestFetchProvisionedModels:
    def _mock_urlopen(self, response_data):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__.return_value = mock_resp
        mock_resp.__exit__.return_value = False
        return mock_resp

    @patch("vertex_quota.urllib.request.urlopen")
    def test_regional_filters_by_region(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_REGIONAL_RESPONSE)
        models = vq._fetch_provisioned_models("proj", "us-east5", "tok")
        assert "anthropic-claude-sonnet-4-5" in models
        assert "anthropic-claude-sonnet-4-6" in models
        assert "anthropic-claude-opus-4-6" in models
        assert len(models) == 4

    @patch("vertex_quota.urllib.request.urlopen")
    def test_regional_excludes_other_regions(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_REGIONAL_RESPONSE)
        models = vq._fetch_provisioned_models("proj", "us-central1", "tok")
        assert len(models) == 1
        assert "anthropic-claude-sonnet-4-5" in models

    @patch("vertex_quota.urllib.request.urlopen")
    def test_global_no_region_filter(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_GLOBAL_RESPONSE)
        models = vq._fetch_provisioned_models("proj", "global", "tok")
        assert len(models) == 4
        assert "anthropic-claude-sonnet-4-5" in models

    @patch("vertex_quota.urllib.request.urlopen")
    def test_regional_uses_regional_metric(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_REGIONAL_RESPONSE)
        vq._fetch_provisioned_models("proj", "us-east5", "tok")
        url = mock_urlopen.call_args[0][0].full_url
        assert "online_prediction_input_tokens" in url
        assert "global_online_prediction" not in url

    @patch("vertex_quota.urllib.request.urlopen")
    def test_global_uses_global_metric(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_GLOBAL_RESPONSE)
        vq._fetch_provisioned_models("proj", "global", "tok")
        url = mock_urlopen.call_args[0][0].full_url
        assert "global_online_prediction" in url

    @patch("vertex_quota.urllib.request.urlopen")
    def test_null_limit_excluded(self, mock_urlopen):
        response = _quota_response([
            _regional_bucket("us-east5", "anthropic-claude-sonnet-4-5", None),
            _regional_bucket("us-east5", "anthropic-claude-sonnet-4-6", "2000000"),
        ])
        mock_urlopen.return_value = self._mock_urlopen(response)
        models = vq._fetch_provisioned_models("proj", "us-east5", "tok")
        assert "anthropic-claude-sonnet-4-5" not in models
        assert "anthropic-claude-sonnet-4-6" in models

    @patch("vertex_quota.urllib.request.urlopen")
    def test_non_anthropic_excluded(self, mock_urlopen):
        response = _quota_response([
            _regional_bucket("us-east5", "google-gemini-pro", "1000000"),
            _regional_bucket("us-east5", "anthropic-claude-sonnet-4-6", "2000000"),
        ])
        mock_urlopen.return_value = self._mock_urlopen(response)
        models = vq._fetch_provisioned_models("proj", "us-east5", "tok")
        assert "google-gemini-pro" not in models
        assert len(models) == 1


# ── check_quota ──────────────────────────────────────────────────────────────


class TestCheckQuota:
    @patch("vertex_quota._fetch_provisioned_models")
    @patch("vertex_quota._get_access_token", return_value="tok")
    @patch("vertex_quota._check_cache", return_value=None)
    def test_model_found_ok(self, _cache, _token, mock_fetch):
        mock_fetch.return_value = {"anthropic-claude-sonnet-4-6": "2000000"}
        result = vq.check_quota("claude-sonnet-4-6", "proj", "us-east5")
        assert result.ok
        assert result.verdict is vq.QuotaVerdict.PROVISIONED
        assert result.model == "anthropic-claude-sonnet-4-6"

    @patch("vertex_quota._fetch_provisioned_models")
    @patch("vertex_quota._get_access_token", return_value="tok")
    @patch("vertex_quota._check_cache", return_value=None)
    def test_model_not_found_fails(self, _cache, _token, mock_fetch):
        mock_fetch.return_value = {
            "anthropic-claude-sonnet-4-5": "2000000",
            "anthropic-claude-sonnet-4-6": "2000000",
        }
        result = vq.check_quota("claude-sonnet-5", "proj", "us-east5")
        assert not result.ok
        assert "claude-sonnet-5" in result.error
        assert "anthropic-claude-sonnet-4-5" in result.available_models

    @patch("vertex_quota._get_access_token", return_value=None)
    @patch("vertex_quota._check_cache", return_value=None)
    def test_no_token_degrades_gracefully(self, _cache, _token):
        result = vq.check_quota("claude-sonnet-5", "proj", "us-east5")
        assert result.ok
        assert result.verdict is vq.QuotaVerdict.UNKNOWN

    @patch("vertex_quota._check_cache")
    def test_cache_hit_found(self, mock_cache):
        mock_cache.return_value = {"anthropic-claude-sonnet-4-6": "2000000"}
        assert vq.check_quota("claude-sonnet-4-6", "proj", "us-east5").ok

    @patch("vertex_quota._check_cache")
    def test_cache_hit_not_found(self, mock_cache):
        mock_cache.return_value = {"anthropic-claude-sonnet-4-6": "2000000"}
        assert not vq.check_quota("claude-sonnet-5", "proj", "us-east5").ok

    @patch("vertex_quota._fetch_provisioned_models", side_effect=urllib.error.URLError("network"))
    @patch("vertex_quota._get_access_token", return_value="tok")
    @patch("vertex_quota._check_cache", return_value=None)
    def test_api_error_degrades_gracefully(self, _cache, _token, _fetch):
        result = vq.check_quota("claude-sonnet-5", "proj", "us-east5")
        assert result.ok
        assert result.verdict is vq.QuotaVerdict.UNKNOWN


# ── Cache ────────────────────────────────────────────────────────────────────


class TestCache:
    def test_fresh_cache_returns_models(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vq, "_CACHE_DIR", tmp_path)
        models = {"anthropic-claude-sonnet-4-6": "2000000"}
        vq._write_cache("proj", "us-east5", models)
        result = vq._check_cache("proj", "us-east5")
        assert result == models

    def test_stale_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vq, "_CACHE_DIR", tmp_path)
        path = vq._cache_key("proj", "us-east5")
        path.write_text(json.dumps({"ts": time.time() - 600, "models": {}}))
        result = vq._check_cache("proj", "us-east5")
        assert result is None

    def test_missing_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vq, "_CACHE_DIR", tmp_path)
        result = vq._check_cache("proj", "us-east5")
        assert result is None

    def test_corrupt_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vq, "_CACHE_DIR", tmp_path)
        path = vq._cache_key("proj", "us-east5")
        path.write_text("not json")
        result = vq._check_cache("proj", "us-east5")
        assert result is None

    def test_symlinked_cache_dir_is_refused(self, tmp_path, monkeypatch):
        """A pre-planted symlink on a shared tmpdir must not be read or written."""
        real = tmp_path / "attacker"
        real.mkdir()
        link = tmp_path / "cache"
        link.symlink_to(real)
        monkeypatch.setattr(vq, "_CACHE_DIR", link)

        assert vq._cache_dir_ready() is False

        vq._write_cache("proj", "us-east5", {"anthropic-claude-sonnet-5": "1"})
        assert list(real.iterdir()) == []
        assert vq._check_cache("proj", "us-east5") is None

    def test_loose_permissions_are_tightened(self, tmp_path, monkeypatch):
        cache = tmp_path / "cache"
        cache.mkdir(mode=0o755)
        monkeypatch.setattr(vq, "_CACHE_DIR", cache)

        assert vq._cache_dir_ready() is True
        assert stat.S_IMODE(cache.stat().st_mode) == 0o700


# ── run_preflight ────────────────────────────────────────────────────────────


class TestRunPreflight:
    def test_skips_when_not_vertex(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_USE_VERTEX", raising=False)
        assert vq.run_preflight({"claude-sonnet-5": ["group"]}, MagicMock()) is True

    def test_skips_when_vertex_not_1(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "0")
        assert vq.run_preflight({"claude-sonnet-5": ["group"]}, MagicMock()) is True

    def test_warns_missing_project(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
        trail = MagicMock()
        assert vq.run_preflight({"claude-sonnet-5": ["group"]}, trail) is True
        trail.info.assert_called()

    def test_warns_missing_region(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
        monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
        trail = MagicMock()
        assert vq.run_preflight({"claude-sonnet-5": ["group"]}, trail) is True

    @patch("vertex_quota.check_quota")
    def test_passes_when_model_found(self, mock_check, monkeypatch):
        _vertex_env(monkeypatch)
        mock_check.return_value = vq.VertexQuotaResult(
            vq.QuotaVerdict.PROVISIONED, "anthropic-claude-sonnet-4-6")
        trail = MagicMock()
        assert vq.run_preflight({"claude-sonnet-4-6": ["group"]}, trail) is True
        trail.info.assert_called()

    @patch("vertex_quota.check_quota")
    def test_fails_when_model_not_provisioned(self, mock_check, monkeypatch):
        _vertex_env(monkeypatch)
        mock_check.return_value = vq.VertexQuotaResult(
            vq.QuotaVerdict.NOT_PROVISIONED, "anthropic-claude-sonnet-5",
            error="no quota",
            available_models=("anthropic-claude-sonnet-4-6",),
        )
        trail = MagicMock()
        assert vq.run_preflight({"claude-sonnet-5": ["scout"]}, trail) is False
        trail.decision.assert_called()

    @patch("vertex_quota.check_quota")
    def test_failure_trail_names_requesting_phases(self, mock_check, monkeypatch):
        _vertex_env(monkeypatch)
        mock_check.return_value = vq.VertexQuotaResult(
            vq.QuotaVerdict.NOT_PROVISIONED, "anthropic-claude-sonnet-5",
            error="no quota")
        trail = MagicMock()
        vq.run_preflight({"claude-sonnet-5": ["scout", "group"]}, trail)
        failures = trail.decision.call_args.kwargs["data"]["failures"]
        assert failures[0]["phases"] == ["scout", "group"]

    def test_failure_names_phase_model_env_keys(self, monkeypatch):
        lines = []
        monkeypatch.setattr(vq.log, "dim", lines.append)
        monkeypatch.setattr(vq.log, "error", lines.append)
        vq._report_failure(
            vq.VertexQuotaResult(
                vq.QuotaVerdict.NOT_PROVISIONED, "anthropic-claude-sonnet-5"),
            [vq.Phase.SCOUT, vq.Phase.GROUP], "proj", "us-east5",
        )
        assert "CLAUDE_REVIEW_SCOUT_MODEL, CLAUDE_REVIEW_GROUP_MODEL" in lines[-1]

    @patch("vertex_quota.check_quota")
    def test_cli_shorthand_skipped_not_failed(self, mock_check, monkeypatch):
        """A bare alias has no Vertex base model name to match against."""
        _vertex_env(monkeypatch)
        trail = MagicMock()
        assert vq.run_preflight({"sonnet": ["group"]}, trail) is True
        mock_check.assert_not_called()

    @patch("vertex_quota.check_quota")
    def test_checks_each_distinct_model_once(self, mock_check, monkeypatch):
        _vertex_env(monkeypatch)
        mock_check.return_value = vq.VertexQuotaResult(
            vq.QuotaVerdict.PROVISIONED, "x")
        vq.run_preflight(
            {"claude-sonnet-5": ["group", "scout"], "claude-opus-5": ["fix"]},
            MagicMock(),
        )
        assert mock_check.call_count == 2
