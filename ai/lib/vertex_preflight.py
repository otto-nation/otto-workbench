"""Vertex AI quota preflight check.

Verifies that resolved models have quota allocations on the configured
Vertex AI project/region before the pipeline spawns any agents.  Catches
misconfigured model aliases (no quota entry) within ~1s instead of
burning ~6 minutes on retries.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

import log
from review_agent import _ANTHROPIC_MODEL_ENV, _resolve_model

_CACHE_TTL_SECS = 300
_CACHE_DIR = Path("/tmp")

_REGIONAL_METRIC = (
    "aiplatform.googleapis.com"
    "%2Fonline_prediction_input_tokens_per_minute_per_base_model"
)
_GLOBAL_METRIC = (
    "aiplatform.googleapis.com"
    "%2Fglobal_online_prediction_input_tokens_per_minute_per_base_model"
)

_PHASE_MODEL_SPECS = [
    ("CLAUDE_REVIEW_SINGLE_MODEL", "sonnet"),
    ("CLAUDE_REVIEW_GROUP_MODEL", "sonnet"),
    ("CLAUDE_REVIEW_HOLISTIC_MODEL", "sonnet"),
    ("CLAUDE_REVIEW_SCOUT_MODEL", "sonnet"),
    ("CLAUDE_REVIEW_SYNTHESIS_MODEL", "sonnet"),
    ("CLAUDE_REVIEW_DISPROVE_MODEL", "sonnet"),
    ("CLAUDE_REVIEW_FIX_MODEL", "sonnet"),
]


@dataclass
class VertexPreflightResult:
    ok: bool
    model: str
    error: str = ""
    available_models: list[str] = field(default_factory=list)


def resolve_vertex_model_id(alias: str) -> str:
    """Resolve a CLI alias to a Vertex base model name.

    'sonnet' -> ANTHROPIC_DEFAULT_SONNET_MODEL -> 'claude-sonnet-5'
               -> 'anthropic-claude-sonnet-5'
    """
    env_key = _ANTHROPIC_MODEL_ENV.get(alias)
    resolved = os.environ.get(env_key) if env_key else None
    model = resolved or alias

    # Strip @version suffix (e.g. claude-haiku-4-5@20251001 -> claude-haiku-4-5)
    if "@" in model:
        model = model.split("@")[0]

    if not model.startswith("anthropic-"):
        model = f"anthropic-{model}"
    return model


def _get_access_token() -> str | None:
    try:
        import google.auth
        import google.auth.transport.requests
        creds, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        creds.refresh(google.auth.transport.requests.Request())
        return creds.token
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["gcloud", "auth", "application-default", "print-access-token"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (FileNotFoundError, subprocess.TimeoutExpired):
        pass
    return None


def _cache_key(project: str, region: str) -> Path:
    raw = f"{project}:{region}"
    h = hashlib.sha256(raw.encode()).hexdigest()[:12]
    return _CACHE_DIR / f"vertex-preflight-{h}.json"


def _check_cache(project: str, region: str) -> dict[str, str] | None:
    path = _cache_key(project, region)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("ts", 0) < _CACHE_TTL_SECS:
            return data.get("models", {})
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _write_cache(project: str, region: str, models: dict[str, str]) -> None:
    path = _cache_key(project, region)
    try:
        path.write_text(json.dumps({"ts": time.time(), "models": models}))
    except OSError:
        pass


def _fetch_provisioned_models(
    project: str, region: str, token: str,
) -> dict[str, str]:
    """Query the Vertex quota API for models with quota allocations.

    Returns {base_model: effective_limit} for Anthropic models.
    """
    is_global = region == "global"
    metric = _GLOBAL_METRIC if is_global else _REGIONAL_METRIC

    url = (
        f"https://serviceusage.googleapis.com/v1beta1"
        f"/projects/{project}/services/aiplatform.googleapis.com"
        f"/consumerQuotaMetrics/{metric}"
    )
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
    })
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    buckets = [
        b for entry in data.get("consumerQuotaLimits", [])
        for b in entry.get("quotaBuckets", [])
    ]
    return _parse_quota_buckets(buckets, region, is_global)


def _parse_quota_buckets(
    buckets: list[dict], region: str, is_global: bool,
) -> dict[str, str]:
    models: dict[str, str] = {}
    for bucket in buckets:
        dims = bucket.get("dimensions") or {}
        base_model = dims.get("base_model")
        if not base_model or not base_model.startswith("anthropic-"):
            continue
        if not is_global and dims.get("region") != region:
            continue
        effective = bucket.get("effectiveLimit")
        if effective is not None:
            models[base_model] = str(effective)
    return models


def check_vertex_quota(
    model_alias: str, project: str, region: str,
) -> VertexPreflightResult:
    base_model = resolve_vertex_model_id(model_alias)

    cached = _check_cache(project, region)
    if cached is not None:
        if base_model in cached:
            return VertexPreflightResult(ok=True, model=base_model)
        available = sorted(cached.keys())
        return VertexPreflightResult(
            ok=False, model=base_model,
            error=f"model '{base_model}' has no quota in project '{project}' region '{region}'",
            available_models=available,
        )

    token = _get_access_token()
    if not token:
        log.warn("Vertex preflight skipped — could not obtain access token")
        return VertexPreflightResult(ok=True, model=base_model)

    try:
        models = _fetch_provisioned_models(project, region, token)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log.warn(f"Vertex preflight skipped — quota API error: {exc}")
        return VertexPreflightResult(ok=True, model=base_model)

    _write_cache(project, region, models)

    if base_model in models:
        return VertexPreflightResult(ok=True, model=base_model)

    available = sorted(models.keys())
    return VertexPreflightResult(
        ok=False, model=base_model,
        error=f"model '{base_model}' has no quota in project '{project}' region '{region}'",
        available_models=available,
    )


def _collect_distinct_models(job_model: str) -> set[str]:
    models = set()
    explicit = job_model or None
    for env_key, default in _PHASE_MODEL_SPECS:
        models.add(_resolve_model(explicit, env_key, default))
    return models


def run_vertex_preflight(job_model: str, trail) -> bool:
    """Run Vertex AI preflight if CLAUDE_CODE_USE_VERTEX=1.

    Returns True if pipeline should proceed, False to abort.
    """
    if os.environ.get("CLAUDE_CODE_USE_VERTEX") != "1":
        return True

    project = os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
    region = os.environ.get("CLOUD_ML_REGION")

    if not project or not region:
        missing = []
        if not project:
            missing.append("ANTHROPIC_VERTEX_PROJECT_ID")
        if not region:
            missing.append("CLOUD_ML_REGION")
        log.warn(f"Vertex preflight skipped — missing env: {', '.join(missing)}")
        trail.info("vertex_preflight", "skipped — missing env vars",
                    data={"missing": missing})
        return True

    models = _collect_distinct_models(job_model)
    failures: list[VertexPreflightResult] = []

    for model in sorted(models):
        result = check_vertex_quota(model, project, region)
        if not result.ok:
            failures.append(result)

    if not failures:
        trail.info("vertex_preflight", "passed",
                    data={"models": sorted(models), "project": project, "region": region})
        return True

    for f in failures:
        log.error(
            f"Vertex AI model '{f.model}' has no quota in"
            f" project '{project}' region '{region}'"
        )
        if f.available_models:
            log.dim(f"  Available: {', '.join(f.available_models)}")
        alias_env = next(
            (env for alias, env in _ANTHROPIC_MODEL_ENV.items()
             if resolve_vertex_model_id(alias) == f.model),
            None,
        )
        if alias_env:
            log.dim(f"  Fix: set {alias_env} to a provisioned model")

    trail.decision(
        "vertex_preflight", "failed — model not provisioned",
        reason="; ".join(f.error for f in failures),
        data={"failures": [{"model": f.model, "available": f.available_models} for f in failures]},
    )
    return False
