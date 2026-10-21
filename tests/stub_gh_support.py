"""A fake `gh` on PATH that records every invocation — the `stub_gh` fixture."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest


def _yq_bin_dir() -> str:
    """Directory of a real `yq` on PATH, skipping version-manager shims.

    `shutil.which("yq")` follows PATH order and often hits a mise/asdf shim
    that is a symlink to the manager, not to yq. Putting that directory on a
    narrowed PATH makes every later `yq` call fail as "No version is set".
    Walk PATH and take the first entry whose resolved basename is still yq.
    """
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        candidate = Path(directory) / "yq"
        try:
            if not candidate.is_file():
                continue
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved.name != "yq":
            continue
        return str(resolved.parent)
    return ""


@pytest.fixture
def stub_gh(tmp_path, monkeypatch):
    """Put a `gh` on PATH whose body is *body*, and record every invocation.

    The stub appends its argv to `calls.txt` before running *body*, so a test
    can assert what the client actually asked for as well as what it did with
    the answer. Shared by `gh_client_test` and `gh_budget_test`, both of which
    need a fake `gh` binary rather than a patched `subprocess` — a mock
    returning the string the test author expected would pass whether or not
    the real call was ever refused.
    """
    def _stub(body: str) -> Path:
        bin_dir = tmp_path / "bin"
        bin_dir.mkdir(exist_ok=True)
        calls = tmp_path / "calls.txt"
        script = bin_dir / "gh"
        script.write_text(
            "#!/bin/sh\n"
            f'printf "%s\\n" "$*" >> {calls}\n'
            f"{body}\n"
        )
        script.chmod(script.stat().st_mode | stat.S_IEXEC)
        # The owner boundary reads github.owners via yq when PyYAML is not in
        # this interpreter; without it every write fails as "config.yml is not
        # readable YAML". Resolve yq where it actually lives before narrowing
        # PATH — Homebrew, /usr/local, a mise install — not a hardcoded dir.
        yq_dir = _yq_bin_dir()
        path_parts = [str(bin_dir)]
        if yq_dir:
            path_parts.append(yq_dir)
        path_parts.extend(["/usr/bin", "/bin"])
        monkeypatch.setenv("PATH", ":".join(path_parts))
        return calls

    return _stub
