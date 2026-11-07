"""A fake `ssh` on PATH that answers `ssh -G ALIAS` — the `ssh_hosts` fixture."""

from __future__ import annotations

import os
import shlex
import sys
from pathlib import Path

import pytest

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")


@pytest.fixture
def ssh_hosts(tmp_path, monkeypatch):
    """Install a stub `ssh`; ``ssh_hosts({alias: hostname})`` returns its call log path.

    An alias missing from the mapping resolves to itself, and ``fail=True`` makes
    the stub exit 255 as ssh does when it cannot answer. The per-process alias
    cache is cleared before and after so no test sees another's answers.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import git.remote_url

    git.remote_url.ssh_hostname.cache_clear()

    def _install(mapping: dict[str, str], fail: bool = False) -> Path:
        directory = tmp_path / "ssh-stub-bin"
        directory.mkdir(exist_ok=True)
        log = tmp_path / "ssh-calls.log"
        arms = "".join(f"  {shlex.quote(a)}) echo hostname {shlex.quote(h)};;\n"
                       for a, h in mapping.items())
        body = ("exit 255\n" if fail else
                f'case "$2" in\n{arms}  *) echo "hostname $2";;\nesac\n')
        stub = directory / "ssh"
        stub.write_text(f'#!/bin/sh\necho "$*" >> {shlex.quote(str(log))}\n{body}')
        stub.chmod(0o755)
        monkeypatch.setenv("PATH", f"{directory}{os.pathsep}{os.environ['PATH']}")
        git.remote_url.ssh_hostname.cache_clear()
        return log

    yield _install
    git.remote_url.ssh_hostname.cache_clear()
