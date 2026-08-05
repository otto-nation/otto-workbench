"""Tests for vertex_preflight module."""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import urllib.error

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"

if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import vertex_preflight as vp


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


# ── resolve_vertex_model_id ──────────────────────────────────────────────────


class TestResolveVertexModelId:
    def test_sonnet_alias_with_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        assert vp.resolve_vertex_model_id("sonnet") == "anthropic-claude-sonnet-5"

    def test_opus_alias_with_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-5")
        assert vp.resolve_vertex_model_id("opus") == "anthropic-claude-opus-5"

    def test_haiku_alias_with_env(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_HAIKU_MODEL", "claude-haiku-4-5@20251001")
        assert vp.resolve_vertex_model_id("haiku") == "anthropic-claude-haiku-4-5"

    def test_alias_without_env_uses_alias(self, monkeypatch):
        monkeypatch.delenv("ANTHROPIC_DEFAULT_SONNET_MODEL", raising=False)
        assert vp.resolve_vertex_model_id("sonnet") == "anthropic-sonnet"

    def test_full_model_name(self):
        assert vp.resolve_vertex_model_id("claude-sonnet-4-6") == "anthropic-claude-sonnet-4-6"

    def test_already_prefixed(self):
        assert vp.resolve_vertex_model_id("anthropic-claude-sonnet-4-6") == "anthropic-claude-sonnet-4-6"

    def test_version_suffix_stripped(self):
        assert vp.resolve_vertex_model_id("claude-haiku-4-5@20251001") == "anthropic-claude-haiku-4-5"


# ── _fetch_provisioned_models ────────────────────────────────────────────────


class TestFetchProvisionedModels:
    def _mock_urlopen(self, response_data):
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(response_data).encode()
        mock_resp.__enter__ = lambda s: s
        mock_resp.__exit__ = MagicMock(return_value=False)
        return mock_resp

    @patch("vertex_preflight.urllib.request.urlopen")
    def test_regional_filters_by_region(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_REGIONAL_RESPONSE)
        models = vp._fetch_provisioned_models("proj", "us-east5", "tok")
        assert "anthropic-claude-sonnet-4-5" in models
        assert "anthropic-claude-sonnet-4-6" in models
        assert "anthropic-claude-opus-4-6" in models
        assert len(models) == 4

    @patch("vertex_preflight.urllib.request.urlopen")
    def test_regional_excludes_other_regions(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_REGIONAL_RESPONSE)
        models = vp._fetch_provisioned_models("proj", "us-central1", "tok")
        assert len(models) == 1
        assert "anthropic-claude-sonnet-4-5" in models

    @patch("vertex_preflight.urllib.request.urlopen")
    def test_global_no_region_filter(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_GLOBAL_RESPONSE)
        models = vp._fetch_provisioned_models("proj", "global", "tok")
        assert len(models) == 4
        assert "anthropic-claude-sonnet-4-5" in models

    @patch("vertex_preflight.urllib.request.urlopen")
    def test_regional_uses_regional_metric(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_REGIONAL_RESPONSE)
        vp._fetch_provisioned_models("proj", "us-east5", "tok")
        url = mock_urlopen.call_args[0][0].full_url
        assert "online_prediction_input_tokens" in url
        assert "global_online_prediction" not in url

    @patch("vertex_preflight.urllib.request.urlopen")
    def test_global_uses_global_metric(self, mock_urlopen):
        mock_urlopen.return_value = self._mock_urlopen(SAMPLE_GLOBAL_RESPONSE)
        vp._fetch_provisioned_models("proj", "global", "tok")
        url = mock_urlopen.call_args[0][0].full_url
        assert "global_online_prediction" in url

    @patch("vertex_preflight.urllib.request.urlopen")
    def test_null_limit_excluded(self, mock_urlopen):
        response = _quota_response([
            _regional_bucket("us-east5", "anthropic-claude-sonnet-4-5", None),
            _regional_bucket("us-east5", "anthropic-claude-sonnet-4-6", "2000000"),
        ])
        mock_urlopen.return_value = self._mock_urlopen(response)
        models = vp._fetch_provisioned_models("proj", "us-east5", "tok")
        assert "anthropic-claude-sonnet-4-5" not in models
        assert "anthropic-claude-sonnet-4-6" in models

    @patch("vertex_preflight.urllib.request.urlopen")
    def test_non_anthropic_excluded(self, mock_urlopen):
        response = _quota_response([
            _regional_bucket("us-east5", "google-gemini-pro", "1000000"),
            _regional_bucket("us-east5", "anthropic-claude-sonnet-4-6", "2000000"),
        ])
        mock_urlopen.return_value = self._mock_urlopen(response)
        models = vp._fetch_provisioned_models("proj", "us-east5", "tok")
        assert "google-gemini-pro" not in models
        assert len(models) == 1


# ── check_vertex_quota ───────────────────────────────────────────────────────


class TestCheckVertexQuota:
    @patch("vertex_preflight._fetch_provisioned_models")
    @patch("vertex_preflight._get_access_token", return_value="tok")
    @patch("vertex_preflight._check_cache", return_value=None)
    def test_model_found_ok(self, _cache, _token, mock_fetch, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6")
        mock_fetch.return_value = {"anthropic-claude-sonnet-4-6": "2000000"}
        result = vp.check_vertex_quota("sonnet", "proj", "us-east5")
        assert result.ok
        assert result.model == "anthropic-claude-sonnet-4-6"

    @patch("vertex_preflight._fetch_provisioned_models")
    @patch("vertex_preflight._get_access_token", return_value="tok")
    @patch("vertex_preflight._check_cache", return_value=None)
    def test_model_not_found_fails(self, _cache, _token, mock_fetch, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        mock_fetch.return_value = {
            "anthropic-claude-sonnet-4-5": "2000000",
            "anthropic-claude-sonnet-4-6": "2000000",
        }
        result = vp.check_vertex_quota("sonnet", "proj", "us-east5")
        assert not result.ok
        assert "claude-sonnet-5" in result.error
        assert "anthropic-claude-sonnet-4-5" in result.available_models

    @patch("vertex_preflight._get_access_token", return_value=None)
    @patch("vertex_preflight._check_cache", return_value=None)
    def test_no_token_degrades_gracefully(self, _cache, _token, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        result = vp.check_vertex_quota("sonnet", "proj", "us-east5")
        assert result.ok

    @patch("vertex_preflight._check_cache")
    def test_cache_hit_found(self, mock_cache, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6")
        mock_cache.return_value = {"anthropic-claude-sonnet-4-6": "2000000"}
        result = vp.check_vertex_quota("sonnet", "proj", "us-east5")
        assert result.ok

    @patch("vertex_preflight._check_cache")
    def test_cache_hit_not_found(self, mock_cache, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        mock_cache.return_value = {"anthropic-claude-sonnet-4-6": "2000000"}
        result = vp.check_vertex_quota("sonnet", "proj", "us-east5")
        assert not result.ok

    @patch("vertex_preflight._fetch_provisioned_models", side_effect=urllib.error.URLError("network"))
    @patch("vertex_preflight._get_access_token", return_value="tok")
    @patch("vertex_preflight._check_cache", return_value=None)
    def test_api_error_degrades_gracefully(self, _cache, _token, _fetch, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        result = vp.check_vertex_quota("sonnet", "proj", "us-east5")
        assert result.ok


# ── Cache ────────────────────────────────────────────────────────────────────


class TestCache:
    def test_fresh_cache_returns_models(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vp, "_CACHE_DIR", tmp_path)
        models = {"anthropic-claude-sonnet-4-6": "2000000"}
        vp._write_cache("proj", "us-east5", models)
        result = vp._check_cache("proj", "us-east5")
        assert result == models

    def test_stale_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vp, "_CACHE_DIR", tmp_path)
        path = vp._cache_key("proj", "us-east5")
        path.write_text(json.dumps({"ts": time.time() - 600, "models": {}}))
        result = vp._check_cache("proj", "us-east5")
        assert result is None

    def test_missing_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vp, "_CACHE_DIR", tmp_path)
        result = vp._check_cache("proj", "us-east5")
        assert result is None

    def test_corrupt_cache_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr(vp, "_CACHE_DIR", tmp_path)
        path = vp._cache_key("proj", "us-east5")
        path.write_text("not json")
        result = vp._check_cache("proj", "us-east5")
        assert result is None


# ── _collect_distinct_models ─────────────────────────────────────────────────


class TestCollectDistinctModels:
    def test_default_config_yields_sonnet(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_MODEL", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_SINGLE_MODEL", raising=False)
        monkeypatch.delenv("CLAUDE_REVIEW_GROUP_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        models = vp._collect_distinct_models("")
        assert models == {"claude-sonnet-5"}

    def test_explicit_model_overrides(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_DEFAULT_OPUS_MODEL", "claude-opus-5")
        models = vp._collect_distinct_models("opus")
        assert models == {"claude-opus-5"}

    def test_phase_env_var_adds_model(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_REVIEW_MODEL", raising=False)
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        monkeypatch.setenv("CLAUDE_REVIEW_SCOUT_MODEL", "claude-sonnet-4-6")
        models = vp._collect_distinct_models("")
        assert "claude-sonnet-5" in models
        assert "claude-sonnet-4-6" in models


# ── run_vertex_preflight ─────────────────────────────────────────────────────


class TestRunVertexPreflight:
    def test_skips_when_not_vertex(self, monkeypatch):
        monkeypatch.delenv("CLAUDE_CODE_USE_VERTEX", raising=False)
        assert vp.run_vertex_preflight("sonnet", MagicMock()) is True

    def test_skips_when_vertex_not_1(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "0")
        assert vp.run_vertex_preflight("sonnet", MagicMock()) is True

    def test_warns_missing_project(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.delenv("ANTHROPIC_VERTEX_PROJECT_ID", raising=False)
        monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
        trail = MagicMock()
        assert vp.run_vertex_preflight("sonnet", trail) is True
        trail.info.assert_called()

    def test_warns_missing_region(self, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
        monkeypatch.delenv("CLOUD_ML_REGION", raising=False)
        trail = MagicMock()
        assert vp.run_vertex_preflight("sonnet", trail) is True

    @patch("vertex_preflight.check_vertex_quota")
    def test_passes_when_model_found(self, mock_check, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
        monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-4-6")
        mock_check.return_value = vp.VertexPreflightResult(ok=True, model="anthropic-claude-sonnet-4-6")
        trail = MagicMock()
        assert vp.run_vertex_preflight("", trail) is True
        trail.info.assert_called()

    @patch("vertex_preflight.check_vertex_quota")
    def test_fails_when_model_not_provisioned(self, mock_check, monkeypatch):
        monkeypatch.setenv("CLAUDE_CODE_USE_VERTEX", "1")
        monkeypatch.setenv("ANTHROPIC_VERTEX_PROJECT_ID", "proj")
        monkeypatch.setenv("CLOUD_ML_REGION", "us-east5")
        monkeypatch.setenv("ANTHROPIC_DEFAULT_SONNET_MODEL", "claude-sonnet-5")
        mock_check.return_value = vp.VertexPreflightResult(
            ok=False, model="anthropic-claude-sonnet-5",
            error="no quota",
            available_models=["anthropic-claude-sonnet-4-6"],
        )
        trail = MagicMock()
        assert vp.run_vertex_preflight("", trail) is False
        trail.decision.assert_called()
