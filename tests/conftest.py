import difflib
import importlib.machinery
import importlib.util
import itertools
import json
import os
import subprocess
import sys
import tempfile
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


def _review_env_keys() -> list[str]:
    """The review-config vars exported right now, by the prefix their owner defines.

    Imported lazily, like the other fixtures that reach into ai/lib: a
    module-scope import here would make every test's collection depend on
    review_common importing cleanly.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    from review_common import REVIEW_ENV_PREFIX
    return [k for k in os.environ if k.startswith(REVIEW_ENV_PREFIX)]


@pytest.fixture(autouse=True)
def _clear_review_env():
    """Run every test with the review config env unset.

    Review model, thinking, and provider settings are read straight from the
    environment with no injection point, so a developer who exports
    CLAUDE_REVIEW_THINKING for their own runs answers those tests' assertions
    from their shell. Modules that resolve config guard themselves today; this
    is the floor, so the next one does not have to remember.

    Matching on the prefix rather than a list is what makes it a floor: the
    per-phase keys are generated from the Phase enum, so a new phase brings new
    keys that no list here would know about. Teardown drops whatever the test
    left behind before restoring, so a test that writes os.environ directly
    cannot leak into the next one either.
    """
    saved = {k: os.environ.pop(k) for k in _review_env_keys()}
    yield
    for key in _review_env_keys():
        del os.environ[key]
    os.environ.update(saved)


REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")


@pytest.fixture(autouse=True)
def _isolate_workbench_config(tmp_path, monkeypatch):
    """Run every test against an empty config root.

    ``workbench_config`` is layers 4 and 5 of the model, thinking, effort and
    issue-tracker chains, so a developer with a populated
    ``~/.config/workbench/config.yml`` would otherwise answer those tests'
    assertions from their own settings. Same floor as ``_clear_review_env``,
    for the file half of the same precedence chain. Tests that want a config
    write one into this root.
    """
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(tmp_path / "workbench-config"))


@pytest.fixture(autouse=True)
def _clear_lock_env():
    """Never inherit a run lock marker across tests, or out of a real run.

    claim_for_process holds its handle until the process exits, which for a
    test process means the rest of the session — so drop those here too.
    Autouse for the same reason as _clear_review_env: this is the floor, so
    the next module that takes a lock does not have to remember.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import run_lock

    saved = os.environ.pop(run_lock.LOCK_ENV, None)
    yield
    for handle in run_lock._HELD:
        handle.close()
    run_lock._HELD.clear()
    os.environ.pop(run_lock.LOCK_ENV, None)
    if saved is not None:
        os.environ[run_lock.LOCK_ENV] = saved


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

# `(section, subsection prefix)` pairs written from outside this process, so a
# change landing mid-test says nothing about the test that was running:
#
#   worktrunk `state.<branch>` — the marker and vars worktrunk restamps whenever
#     an agent's status changes; `hints` — counters for the one-time hints it has
#     shown. Deliberately not the whole namespace: `worktrunk.default-branch` is
#     user config (bin/wt-cleanup reads it), so a test clobbering it must fail.
#   `branch` — tracking entries, which every concurrent fetch, branch create, and
#     `wt switch` across the shared worktrees adds and prunes. A test that leaks
#     into the real repo writes its identity before it ever reaches a branch, and
#     that write is still caught, so exempting these costs the guard nothing.
_EXTERNAL_STATE = (
    (b"worktrunk", b"state."), (b"worktrunk", b"hints"), (b"branch", b""),
)


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

    The state in `_EXTERNAL_STATE` is exempt: it is written concurrently by
    tooling this process does not control — worktrunk restamps its per-branch
    state whenever a session hook fires, including mid-test-run — and blaming
    the running test for those writes turns every long test run into a coin
    flip. Parsing is deferred until the bytes actually differ, so the common
    case stays two reads.
    """
    if _REPO_CONFIG is None:
        yield
        return
    before = _config_bytes(_REPO_CONFIG)
    yield
    _assert_config_unchanged(_REPO_CONFIG, before, _config_bytes(_REPO_CONFIG))


REVIEW_POST = REPO_ROOT / "ai" / "claude" / "bin" / "review-post"
REVIEW_ORCHESTRATE = REPO_ROOT / "ai" / "claude" / "bin" / "review-orchestrate"
REVIEW_THREADS = REPO_ROOT / "ai" / "claude" / "bin" / "review-threads"
CI_CHECK = REPO_ROOT / "ai" / "claude" / "bin" / "ci-check"
EVAL_MODELS = REPO_ROOT / "ai" / "claude" / "bin" / "eval-models"


def init_worktree(path) -> Path:
    """Make *path* a git worktree and return it.

    Per-worktree state lives in the worktree's own git dir, so a bare tmp_path
    is no longer a stand-in for a worktree — `git rev-parse` has to answer for
    it. No identity is configured: a test that commits sets its own, and the
    repo-config guard exists to catch the one that forgets.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-b", "main", "-q", str(path)],
                   check=True, capture_output=True)
    return path


@pytest.fixture
def worktree(tmp_path) -> Path:
    """tmp_path as a git worktree, for tests that pass it as a worktree root."""
    return init_worktree(tmp_path)


def synthetic_review(
    meta: str = "generator: test",
    summary: str = "Synthesized.",
    findings: str = "",
    verdict: str = "Approve",
) -> str:
    """The smallest review document a finished run can leave behind.

    Every suite that only needs "a review file exists here" writes the same
    shape — title, one meta comment, a summary, an optional findings section,
    a verdict — so it lives here instead of once per module. The parameters are
    the parts callers have actually needed to differ on; anything else being
    identical is the point, since these bodies stand in for the deliverable
    rather than exercising the parser.
    """
    findings_block = f"{findings}\n" if findings else ""
    return (
        "# Review: org/repo#1 — t\n"
        f"<!-- {meta} -->\n"
        f"## Summary\n{summary}\n\n"
        f"{findings_block}"
        f"## Verdict\n{verdict}\n"
    )


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
def _isolate_trail_root(tmp_path, monkeypatch):
    """Point the whole state root at a temp dir for the duration of every test.

    Every trail writer appends under this root, so an unsandboxed run would
    interleave test records with the developer's real history. The environment
    is set rather than an attribute patched because that is the only form a
    tool invoked as a subprocess inherits, and because `state_dir()` resolves
    per call instead of freezing at import.
    """
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))


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
    otherwise leave the next one free to write to GitHub for real. The hold is
    cleared for the opposite reason: it never reopens within a process, so one
    test that sets it would close the gate for every test after it.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import publishing
    monkeypatch.setattr(publishing, "_enabled", False)
    monkeypatch.setattr(publishing, "_held", "")


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


# One temp root for every make_ctx() default target_dir, not a tmp_path-scoped
# one: make_ctx is a plain factory most callers invoke without a tmp_path
# fixture at all. A single TemporaryDirectory's finalizer removes the whole tree
# at interpreter exit; handing out a numbered subpath per call keeps the callers
# that never touch target_dir from colliding with each other or with the
# lock-wiring tests that override it anyway.
_CTX_TARGET_ROOT = tempfile.TemporaryDirectory(prefix="workbench-test-target-")
_ctx_target_seq = itertools.count()


def make_ctx(**overrides):
    """Build a minimal ResolvedContext, for any test that needs one to call with.

    Shared rather than per-module so that the next required field on
    ResolvedContext lands in one place instead of once per test file.

    target_dir defaults to a fresh, unique subpath under _CTX_TARGET_ROOT, not a
    fixed placeholder: run_lock.acquire unconditionally mkdir(parents=True)s it,
    so a shared, non-writable default like Path("/target") would fail the moment
    a test drives an entry point through a mutating command without overriding
    it. The subpath itself is never created here — acquire creates it on demand,
    same as a real run would.
    worktree_root keeps a symbolic Path("/wt") default: tests that read or write
    a real checkout pass their own, and for the rest it only ever appears in
    string comparisons (e.g. --repo-dir) and mocked subprocess calls.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import pr_context

    defaults = dict(
        repo="owner/repo", branch="feat/test", pr_number=42,
        worktree_root=Path("/wt"), head_sha="abc123",
        target_dir=Path(_CTX_TARGET_ROOT.name) / f"target-{next(_ctx_target_seq)}",
    )
    defaults.update(overrides)
    return pr_context.ResolvedContext(**defaults)


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
