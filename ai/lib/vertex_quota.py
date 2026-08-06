"""Vertex AI quota checks for the Claude Code backend.

Verifies that the models a run would use have quota allocations on the
configured Vertex AI project/region before any agent is spawned.  Catches
misconfigured model ids (nothing the project can serve, not even the model's
family) within ~1s instead of burning ~6 minutes on retries.

Reached through ``ai_backend.preflight()`` — nothing outside the Claude
backend should import this module.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

import log
from review_common import Phase

try:
    from google.auth import default as _google_auth_default
    from google.auth.exceptions import GoogleAuthError as _GoogleAuthError
    from google.auth.transport.requests import Request as _GoogleAuthRequest
    _HAS_GOOGLE_AUTH = True
except ImportError:
    _GoogleAuthError = Exception
    _HAS_GOOGLE_AUTH = False

_CACHE_TTL_SECS = 300
_CACHE_DIR = Path(tempfile.gettempdir()) / f"vertex-quota-{os.getuid()}"

_REGIONAL_METRIC = (
    "aiplatform.googleapis.com"
    "%2Fonline_prediction_input_tokens_per_minute_per_base_model"
)
_GLOBAL_METRIC = (
    "aiplatform.googleapis.com"
    "%2Fglobal_online_prediction_input_tokens_per_minute_per_base_model"
)

# Vertex namespaces Anthropic models under the publisher prefix, so quota
# buckets are keyed as e.g. "anthropic-claude-sonnet-5".
_PUBLISHER_PREFIX = "anthropic-"
_MODEL_ID_PREFIX = "claude-"


class QuotaVerdict(StrEnum):
    """What a quota lookup established about one model.

    ``UNKNOWN`` is distinct from ``PROVISIONED``: the check could not run
    (no credentials, API error), so nothing was proven either way.
    """

    PROVISIONED = "provisioned"
    NOT_PROVISIONED = "not_provisioned"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class VertexQuotaResult:
    verdict: QuotaVerdict
    model: str
    error: str = ""
    available_models: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        """Whether the run may proceed — only a proven gap blocks it."""
        return self.verdict is not QuotaVerdict.NOT_PROVISIONED


def is_checkable(model: str) -> bool:
    """Whether a model string is a concrete id we can match against quota.

    CLI shorthands ("sonnet", "opus") only become concrete model ids inside
    the Claude Code CLI, so there is nothing to look up — treat them as
    unknown rather than reporting a bogus missing-quota failure.
    """
    return model.startswith((_MODEL_ID_PREFIX, _PUBLISHER_PREFIX))


def resolve_vertex_model_id(model: str) -> str:
    """Map a concrete model id to its Vertex base model name.

    'claude-sonnet-5' -> 'anthropic-claude-sonnet-5'
    """
    # Strip @version suffix (e.g. claude-haiku-4-5@20251001 -> claude-haiku-4-5)
    if "@" in model:
        model = model.split("@")[0]

    if not model.startswith(_PUBLISHER_PREFIX):
        model = f"{_PUBLISHER_PREFIX}{model}"
    return model


def _get_access_token() -> str | None:
    if _HAS_GOOGLE_AUTH:
        try:
            creds, _ = _google_auth_default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(_GoogleAuthRequest())
            return creds.token
        except (_GoogleAuthError, OSError) as exc:
            log.dim(f"google-auth unavailable ({exc}) — falling back to gcloud")
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
    return _CACHE_DIR / f"vertex-quota-{h}.json"


def _cache_dir_ready() -> bool:
    """Whether the cache dir is a real, private directory we own.

    The path lives under a world-writable tmpdir on Linux, so another user can
    pre-plant it as a symlink into a directory of their choosing. Anything that
    is not a plain self-owned directory is refused outright — the cache is an
    optimisation, and skipping it just means one more API call.
    """
    try:
        _CACHE_DIR.mkdir(parents=True, exist_ok=True, mode=0o700)
        st = os.lstat(_CACHE_DIR)
    except OSError:
        return False
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid():
        return False
    if st.st_mode & 0o077:
        os.chmod(_CACHE_DIR, 0o700)
    return True


def _check_cache(project: str, region: str) -> dict[str, str] | None:
    if not _cache_dir_ready():
        return None
    path = _cache_key(project, region)
    if not path.is_file() or path.is_symlink():
        return None
    try:
        data = json.loads(path.read_text())
        if time.time() - data.get("ts", 0) < _CACHE_TTL_SECS:
            return data.get("models", {})
    except (json.JSONDecodeError, OSError):
        pass
    return None


def _atomic_write(path: Path, payload: str) -> None:
    fd, tmp_path = tempfile.mkstemp(dir=_CACHE_DIR, prefix=".tmp-")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(payload)
        os.replace(tmp_path, path)
    except OSError:
        os.unlink(tmp_path)
        raise


def _write_cache(project: str, region: str, models: dict[str, str]) -> None:
    if not _cache_dir_ready():
        return
    try:
        _atomic_write(
            _cache_key(project, region),
            json.dumps({"ts": time.time(), "models": models}),
        )
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
        if not base_model or not base_model.startswith(_PUBLISHER_PREFIX):
            continue
        if not is_global and dims.get("region") != region:
            continue
        effective = bucket.get("effectiveLimit")
        if effective is not None:
            models[base_model] = str(effective)
    return models


def check_quota(model: str, project: str, region: str) -> VertexQuotaResult:
    base_model = resolve_vertex_model_id(model)

    cached = _check_cache(project, region)
    if cached is not None:
        return _verdict(base_model, cached, project, region)

    token = _get_access_token()
    if not token:
        log.warn("Vertex quota check skipped — could not obtain access token")
        return VertexQuotaResult(QuotaVerdict.UNKNOWN, base_model)

    try:
        models = _fetch_provisioned_models(project, region, token)
    except (urllib.error.URLError, OSError, json.JSONDecodeError) as exc:
        log.warn(f"Vertex quota check skipped — quota API error: {exc}")
        return VertexQuotaResult(QuotaVerdict.UNKNOWN, base_model)

    _write_cache(project, region, models)
    return _verdict(base_model, models, project, region)


def _covering_bucket(base_model: str, provisioned: Mapping[str, str]) -> str | None:
    """Find the quota bucket a model draws on, or None if the project has none.

    Vertex publishes a bucket per model version ("anthropic-claude-sonnet-4-6")
    alongside one per family ("anthropic-claude-sonnet"). A version released
    after the project's buckets were last enumerated has no bucket of its own
    and serves under its family's allocation, so an exact-key miss does not
    mean the model is unusable — it usually means the model is new.

    Walk from the most specific name to the least and take the first bucket
    that exists. A name whose family is unknown to the project matches nothing,
    which is the case worth blocking: a typo, or a model this project genuinely
    cannot serve.
    """
    parts = base_model.split("-")
    for cut in range(len(parts), 0, -1):
        candidate = "-".join(parts[:cut])
        if candidate in provisioned:
            return candidate
    return None


def _verdict(
    base_model: str, provisioned: dict[str, str], project: str, region: str,
) -> VertexQuotaResult:
    if _covering_bucket(base_model, provisioned) is not None:
        return VertexQuotaResult(QuotaVerdict.PROVISIONED, base_model)
    return VertexQuotaResult(
        QuotaVerdict.NOT_PROVISIONED, base_model,
        error=f"model '{base_model}' has no quota in project '{project}' region '{region}'",
        available_models=tuple(sorted(provisioned.keys())),
    )


def _report_failure(
    result: VertexQuotaResult, phases: Sequence[str], project: str, region: str,
) -> None:
    log.error(
        f"Vertex AI model '{result.model}' has no quota in"
        f" project '{project}' region '{region}'"
    )
    if result.available_models:
        log.dim(f"  Available: {', '.join(result.available_models)}")
    keys = ", ".join(Phase(p).model_env_key for p in phases)
    log.dim(f"  Fix: set {keys} to a provisioned model")


def run_preflight(models: Mapping[str, Sequence[str]], trail) -> bool:
    """Check every model the run would use against Vertex quota.

    ``models`` maps a resolved model id to the ``Phase`` names requesting it.
    Returns True if the run should proceed, False to abort.  Any condition that leaves
    quota unknown (not on Vertex, missing config, no credentials, API error)
    proceeds — this gate only stops runs it can prove are misconfigured.
    """
    if os.environ.get("CLAUDE_CODE_USE_VERTEX") != "1":
        return True

    missing = [
        key for key in ("ANTHROPIC_VERTEX_PROJECT_ID", "CLOUD_ML_REGION")
        if not os.environ.get(key)
    ]
    if missing:
        log.warn(f"Vertex quota check skipped — missing env: {', '.join(missing)}")
        trail.info("vertex_quota", "skipped — missing env vars",
                   data={"missing": missing})
        return True

    project = os.environ["ANTHROPIC_VERTEX_PROJECT_ID"]
    region = os.environ["CLOUD_ML_REGION"]

    skipped = sorted(m for m in models if not is_checkable(m))
    if skipped:
        log.dim(f"Vertex quota check skipped for CLI shorthand: {', '.join(skipped)}")

    failures: list[tuple[VertexQuotaResult, Sequence[str]]] = []
    checked = sorted(m for m in models if is_checkable(m))
    for model in checked:
        result = check_quota(model, project, region)
        if not result.ok:
            failures.append((result, models[model]))

    if not failures:
        trail.info("vertex_quota", "passed",
                   data={"checked": checked, "skipped": skipped,
                         "project": project, "region": region})
        return True

    for result, phases in failures:
        _report_failure(result, phases, project, region)

    trail.decision(
        "vertex_quota", "failed — model not provisioned",
        reason="; ".join(result.error for result, _ in failures),
        data={"failures": [
            {"model": result.model, "phases": list(phases),
             "available": list(result.available_models)}
            for result, phases in failures
        ]},
    )
    return False
