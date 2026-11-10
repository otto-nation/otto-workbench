"""Tests for generate-herdr-app-themes brand tarball fetch retries."""

from __future__ import annotations

import base64
import hashlib
import io
import socket
import tarfile
import urllib.error
from pathlib import Path

import pytest
from conftest import REPO_ROOT, load_script

themes = load_script(
    "generate_herdr_app_themes",
    REPO_ROOT / "bin" / "local" / "generate-herdr-app-themes",
)


def _blob() -> bytes:
    tokens = b'{"schemaVersion":1,"colors":{"light":{"canvas":"#112233"},"dark":{}}}\n'
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        info = tarfile.TarInfo("package/tokens.json")
        info.size = len(tokens)
        tar.addfile(info, io.BytesIO(tokens))
    return buf.getvalue()


def _pin(blob: bytes):
    integrity = "sha512-" + base64.b64encode(hashlib.sha512(blob).digest()).decode()
    return themes.BrandPin(
        url="https://example.invalid/brand-1.2.0.tgz",
        version="1.2.0",
        integrity=integrity,
    )


def _http(code: int, msg: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://example.invalid/brand-1.2.0.tgz", code, msg, {}, None,
    )


def _patch_fetch(monkeypatch, tmp_path: Path, outcomes: list):
    sleeps: list[float] = []
    calls: list[object] = []
    remaining = list(outcomes)

    def fake(*_args, **_kwargs):
        calls.append(1)
        outcome = remaining.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return io.BytesIO(outcome)

    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setattr(themes.time, "sleep", sleeps.append)
    monkeypatch.setattr("urllib.request.urlopen", fake)
    return sleeps, calls


def test_retries_504_then_succeeds(monkeypatch, tmp_path):
    blob = _blob()
    pin = _pin(blob)
    sleeps, calls = _patch_fetch(
        monkeypatch, tmp_path, [_http(504, "Gateway Timeout"), blob],
    )
    loaded = themes._fetch(pin)
    assert loaded.tokens["light"]["ow-canvas"] == "#112233"
    assert sleeps == [themes.FETCH_BACKOFF]
    assert len(calls) == 2


def test_three_504s_raise_after_three_calls(monkeypatch, tmp_path):
    pin = _pin(_blob())
    err = _http(504, "Gateway Timeout")
    sleeps, calls = _patch_fetch(monkeypatch, tmp_path, [err, err, err])
    with pytest.raises(themes.BrandError, match="unavailable"):
        themes._fetch(pin)
    assert len(calls) == 3
    assert sleeps == [themes.FETCH_BACKOFF, themes.FETCH_BACKOFF * 2]


def test_404_raises_without_retry(monkeypatch, tmp_path):
    pin = _pin(_blob())
    sleeps, calls = _patch_fetch(
        monkeypatch, tmp_path, [_http(404, "Not Found")],
    )
    with pytest.raises(themes.BrandError, match="unavailable"):
        themes._fetch(pin)
    assert len(calls) == 1
    assert sleeps == []


def test_timeout_then_success(monkeypatch, tmp_path):
    blob = _blob()
    pin = _pin(blob)
    sleeps, calls = _patch_fetch(monkeypatch, tmp_path, [TimeoutError(), blob])
    loaded = themes._fetch(pin)
    assert loaded.tokens["light"]["ow-canvas"] == "#112233"
    assert sleeps == [themes.FETCH_BACKOFF]
    assert len(calls) == 2


def test_old_cache_layout_is_ignored(tmp_path, monkeypatch):
    blob = _blob()
    pin = _pin(blob)
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    old = tmp_path / "state" / "herdr-app-themes" / "2" / "brand-1.2.0.tgz"
    old.parent.mkdir(parents=True)
    old.write_bytes(blob)
    assert themes._cached(pin) is None
    new = themes._cache_path(pin)
    new.parent.mkdir(parents=True, exist_ok=True)
    new.write_bytes(blob)
    loaded = themes._cached(pin)
    assert loaded is not None
    assert loaded.source == "cache"
    assert loaded.tokens["light"]["ow-canvas"] == "#112233"
    assert new == tmp_path / "state" / "herdr-app-themes" / "3" / "brand-1.2.0.tgz"


def test_transient_ignores_permanent_oserrors():
    assert not themes._transient(FileNotFoundError("gone"))
    assert not themes._transient(urllib.error.URLError(FileNotFoundError("gone")))
    assert themes._transient(TimeoutError())
    assert themes._transient(urllib.error.URLError(ConnectionResetError()))
    assert themes._transient(urllib.error.URLError(socket.gaierror(-3, "try again")))
