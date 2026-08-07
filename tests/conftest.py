import difflib
import importlib.machinery
import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

_GIT_HOOK_VARS = (
    "GIT_DIR", "GIT_WORK_TREE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


@pytest.fixture(autouse=True, scope="session")
def _clear_git_hook_env():
    """Clear git env vars inherited from hooks (e.g. pre-push)."""
    saved = {k: os.environ.pop(k) for k in _GIT_HOOK_VARS if k in os.environ}
    yield
    os.environ.update(saved)


REPO_ROOT = Path(__file__).resolve().parent.parent


def _repo_config_path():
    """The shared git config of the repo under test, or None if unresolvable.

    In a worktree, ``.git`` is a file pointing at ``<common>/worktrees/<name>``;
    the config lives two levels up, in the common dir.
    """
    dot_git = REPO_ROOT / ".git"
    if dot_git.is_dir():
        return dot_git / "config"
    if not dot_git.is_file():
        return None
    gitdir = Path(dot_git.read_text().split(":", 1)[1].strip())
    return gitdir.parent.parent / "config"


_REPO_CONFIG = _repo_config_path()

# `(section, subsection prefix)` pairs holding runtime state that worktrunk
# rewrites from outside this process — `state.<branch>` carries the marker and
# vars it restamps whenever an agent's status changes, `hints` the counters for
# one-time hints it has shown. A write landing mid-test says nothing about the
# test that happened to be running.
#
# Deliberately not the whole `worktrunk` namespace: `worktrunk.default-branch`
# is user config (bin/wt-cleanup reads it), so a test clobbering it must fail.
_EXTERNAL_STATE = ((b"worktrunk", b"state."), (b"worktrunk", b"hints"))


def _section_of(line: bytes) -> tuple[bytes, bytes] | None:
    """The `(section, subsection)` a `[header]` line opens, else None."""
    if not line.startswith(b"["):
        return None
    head, _, quoted = line[1:].partition(b'"')
    return head.strip(b"]").strip(), quoted.rsplit(b'"', 1)[0] if quoted else b""


def _is_external(section: bytes, subsection: bytes) -> bool:
    return any(section == name and subsection.startswith(prefix)
               for name, prefix in _EXTERNAL_STATE)


def _guarded_lines(raw: bytes | None) -> list[bytes] | None:
    """The config's lines with the externally-owned state dropped."""
    if raw is None:
        return None
    kept, external = [], False
    for line in raw.splitlines():
        opened = _section_of(line.strip())
        if opened is not None:
            external = _is_external(*opened)
        if not external:
            kept.append(line)
    return kept


def _describe_config_change(before: list[bytes], after: list[bytes]) -> str:
    """The lines that came and went, so the failure names the key it caught.

    A whole-file byte diff of a 30 KB config reports an offset and nothing a
    reader can act on. `n=0` keeps the surrounding 600-odd untouched lines out
    of the message.
    """
    diff = difflib.unified_diff(
        [line.decode(errors="replace").strip() for line in before],
        [line.decode(errors="replace").strip() for line in after],
        n=0, lineterm="",
    )
    return "\n".join(line for line in diff if not line.startswith(("---", "+++")))


def _config_bytes(path: Path) -> bytes | None:
    return path.read_bytes() if path.exists() else None


def _assert_config_unchanged(path: Path, before: bytes | None, after: bytes | None):
    """Raise unless every change to `path` belongs to an external section."""
    if after == before:
        return
    guarded_before, guarded_after = _guarded_lines(before), _guarded_lines(after)
    assert guarded_after == guarded_before, (
        f"test wrote git config into the real repo: {path}\n"
        f"{_describe_config_change(guarded_before or [], guarded_after or [])}"
    )


@pytest.fixture(autouse=True)
def _guard_repo_config():
    """Fail the test that writes git config into the repo under test.

    Tests build throwaway repos under tmp_path, but a stray GIT_DIR or a
    relative cwd sends `git config` to the real repo instead. Because worktrees
    share one config file, the damage is repo-wide and permanent: every later
    commit inherits the test identity.

    Sections in `_EXTERNAL_SECTIONS` are exempt: they are written concurrently
    by tooling this process does not control, and blaming the running test for
    those turns every long test run into a coin flip.
    """
    if _REPO_CONFIG is None:
        yield
        return
    before = _config_bytes(_REPO_CONFIG)
    yield
    _assert_config_unchanged(_REPO_CONFIG, before, _config_bytes(_REPO_CONFIG))


LIB_DIR = str(REPO_ROOT / "ai" / "lib")
REVIEW_POST = REPO_ROOT / "ai" / "claude" / "bin" / "review-post"
REVIEW_ORCHESTRATE = REPO_ROOT / "ai" / "claude" / "bin" / "review-orchestrate"
REVIEW_THREADS = REPO_ROOT / "ai" / "claude" / "bin" / "review-threads"
CI_CHECK = REPO_ROOT / "ai" / "claude" / "bin" / "ci-check"
EVAL_MODELS = REPO_ROOT / "ai" / "claude" / "bin" / "eval-models"


def write_thrash_log(path) -> str:
    """Write the session log of a clean completion that never wrote anything.

    This is the shape the shared thrash guard exists to catch — the agent ended
    on its own terms, so there is no error to blame it for. Every `pr` script
    that drives an agent needs the same fixture, so it lives here rather than
    once per test module. Returns the path as a str, which is what the guard's
    callers take.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join([
        json.dumps({"type": "assistant", "message": {"content": [
            {"type": "tool_use", "name": "Read", "input": {}},
        ]}}),
        json.dumps({"type": "result", "subtype": "success", "num_turns": 3}),
    ]) + "\n")
    return str(path)


# Session-scoped: the module is loaded once and shared across all tests.
# Tests must not mutate module-level state.
@pytest.fixture(scope="session")
def rp():
    loader = importlib.machinery.SourceFileLoader("review_post", str(REVIEW_POST))
    spec = importlib.util.spec_from_loader("review_post", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["review_post"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["review_post"]


@pytest.fixture(autouse=True)
def _isolate_usage_ledger(tmp_path, monkeypatch):
    """Point the global usage ledger at a temp dir for the duration of every test.

    ai_backend records usage on every entry point, so any test that reaches the real
    dispatch layer would otherwise append junk rows to the developer's own ledger.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import ai_usage
    monkeypatch.setattr(ai_usage, "LEDGER_DIR", tmp_path / "usage-ledger")


@pytest.fixture(autouse=True)
def _drafts_only(monkeypatch):
    """Close the publishing gate for every test.

    The gate is a single process-global flag, so one test that opens it would
    otherwise leave the next one free to write to GitHub for real.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import publishing
    monkeypatch.setattr(publishing, "_enabled", False)


@pytest.fixture
def publishing_on(monkeypatch):
    """Open the gate, for tests covering what a write does once it is allowed."""
    import publishing
    monkeypatch.setattr(publishing, "_enabled", True)


@pytest.fixture(autouse=True)
def _clear_bot_login_cache():
    """Clear _get_bot_login lru_cache between tests."""
    yield
    if LIB_DIR in sys.path or "review_dedup" in sys.modules:
        try:
            import review_dedup
            review_dedup._get_bot_login.cache_clear()
        except (ImportError, AttributeError):
            pass


@pytest.fixture(scope="session")
def ro():
    loader = importlib.machinery.SourceFileLoader("review_orchestrate", str(REVIEW_ORCHESTRATE))
    spec = importlib.util.spec_from_loader("review_orchestrate", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["review_orchestrate"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["review_orchestrate"]


@pytest.fixture(scope="session")
def rt():
    loader = importlib.machinery.SourceFileLoader("review_threads", str(REVIEW_THREADS))
    spec = importlib.util.spec_from_loader("review_threads", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["review_threads"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["review_threads"]


@pytest.fixture(scope="session")
def em():
    loader = importlib.machinery.SourceFileLoader("eval_models", str(EVAL_MODELS))
    spec = importlib.util.spec_from_loader("eval_models", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["eval_models"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["eval_models"]


@pytest.fixture(scope="session")
def cc():
    loader = importlib.machinery.SourceFileLoader("ci_check", str(CI_CHECK))
    spec = importlib.util.spec_from_loader("ci_check", loader)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["ci_check"] = mod
    spec.loader.exec_module(mod)
    yield mod
    del sys.modules["ci_check"]


def assert_no_worktree_exit(capsys, branch, fn, *args, **kwargs):
    """Assert *fn* refuses to run without a worktree, and says what to do.

    Every entry point that calls ResolvedContext.require_worktree() fails the
    same way by design, so the message is asserted from one place — the point
    of the accessor is that there is exactly one message to get right.
    """
    with pytest.raises(SystemExit) as exc:
        fn(*args, **kwargs)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"No worktree for {branch!r}" in err
    assert f"wt switch {branch}" in err
    assert "--repo-dir" in err
