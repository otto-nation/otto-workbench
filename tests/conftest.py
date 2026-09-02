import difflib
import importlib.machinery
import importlib.util
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = str(REPO_ROOT / "ai" / "lib")


_LIBS: dict[str, object] = {}


def _load_lib(name: str):
    """Import `lib/<name>.py` once, without putting `lib/` on `sys.path`.

    `lib/` holds modules named for what they wrap rather than for this repo, so
    adding it to the path would let one of them answer an unrelated import. The
    cache is what makes two callers asking for the same name share one module
    rather than hold copies whose state can drift apart.
    """
    if name not in _LIBS:
        spec = importlib.util.spec_from_file_location(name, REPO_ROOT / "lib" / f"{name}.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        _LIBS[name] = module
    return _LIBS[name]


# The same cache for the other half of the tree. Scripts under `bin/`,
# `bin/local/` and `ai/claude/bin/` carry no `.py` extension, so a test cannot
# import one — it has to execute the file. Two callers executing the same file
# hold two module objects for one script, and the two are not interchangeable:
# `mock.patch("<name>.f")` resolves its target through `sys.modules`, while a
# test that kept its own reference calls straight into the module it built. The
# patch lands on one copy and the call reads the other, so a mock silently does
# nothing — a failure that appears only when both callers are live in one
# process, which under `pytest -n` depends on how the files were distributed.
_SCRIPTS: dict[str, object] = {}
_SCRIPT_OWNERS: dict[Path, str] = {}


def load_script(name: str, path) -> object:
    """Execute the script at *path* once, as module *name*, and return it.

    Every caller asking for *name* gets that one module object, and it is the
    one `sys.modules[name]` holds, so a string patch target and a held
    reference always mean the same module.

    Registration happens before execution and is never undone. A dataclass
    resolves a string annotation through `sys.modules[cls.__module__]` while
    its class body runs, which is why the name has to be there first; and a
    name dropped at session teardown would strand every module still holding a
    reference to a script the interpreter no longer answers for.

    The name and the file are held as a pair, so a name already taken by
    another file — or a file already loaded under another name — raises instead
    of quietly producing the second copy. `sys.argv` is pinned to the script's
    own name for the execution, since a script that reads arguments at import
    time would otherwise read pytest's.
    """
    path = Path(path).resolve()
    owner = _SCRIPT_OWNERS.get(path)
    if owner is not None and owner != name:
        raise RuntimeError(
            f"{path} is already loaded as {owner!r}, so asking for it as {name!r} "
            f"would build a second module object for one script. Ask for {owner!r}."
        )
    taken = next((p for p, n in _SCRIPT_OWNERS.items() if n == name), None)
    if taken is not None and taken != path:
        raise RuntimeError(
            f"module name {name!r} already belongs to {taken}, so {path} needs a "
            f"name of its own."
        )
    if name in _SCRIPTS:
        return _SCRIPTS[name]
    module = _exec_module(name, path)
    _SCRIPTS[name] = module
    _SCRIPT_OWNERS[path] = name
    return module


def exec_fresh(name: str, path):
    """Execute *path* as a throwaway module, for a test whose subject is import time.

    The deliberate opposite of `load_script`: a module that reads the
    environment while its body runs answers whatever the environment said then,
    so a test that changes the environment has to execute it again. The name is
    registered for the duration of the execution only — the same dataclass
    lookup `load_script` describes — and whatever `sys.modules` answered with
    beforehand is put back, on a failed execution as well as a clean one.

    A name `load_script` already owns is refused rather than borrowed. Running a
    throwaway copy under it would point `sys.modules[name]` at that copy for the
    duration while `_SCRIPTS` still held the shared one, which is the split
    between a string patch target and a held reference this owner exists to
    close.
    """
    owned = _SCRIPTS.get(name)
    if owned is not None:
        raise RuntimeError(
            f"module name {name!r} is owned by load_script ({owned.__file__}), so "
            f"executing a throwaway copy under it would displace the module the "
            f"rest of the suite shares. Give the copy a name of its own."
        )
    displaced = sys.modules.get(name)
    try:
        return _exec_module(name, Path(path).resolve())
    finally:
        # pop rather than del: a failed execution has already released the name.
        if displaced is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = displaced


def _exec_module(name: str, path: Path):
    """Build the module, register the name, run the body, and hand it back.

    On a failed execution the name is released again. Leaving a half-executed
    module registered would hand the next caller a module that imports cleanly
    and behaves like nothing in the file, which reads as a defect somewhere
    else entirely.
    """
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader, origin=str(path))
    module = importlib.util.module_from_spec(spec)
    module.__file__ = str(path)
    sys.modules[name] = module
    saved_argv = sys.argv
    sys.argv = [path.name]
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[name]
        raise
    finally:
        sys.argv = saved_argv
    return module


# Which variables redirect git at another repository is one fact, and
# lib/gitenv.py owns it — lib/gitenv.sh and every gate under bin/local/ already
# read the same list. A hand-copy here stood a variable short of it, so a
# leaked GIT_INDEX_FILE would have staged a test's files into the real index.
_GIT_HOOK_VARS = _load_lib("gitenv").GIT_ENV_OVERRIDES


@pytest.fixture(autouse=True, scope="session")
def _clear_git_hook_env():
    """Clear git env vars inherited from hooks (e.g. pre-push)."""
    saved = {k: os.environ.pop(k) for k in _GIT_HOOK_VARS if k in os.environ}
    yield
    os.environ.update(saved)


def _agent_env_keys() -> list[str]:
    """The agent-config vars exported right now, by the prefix their owner defines.

    Imported lazily, like the other fixtures that reach into ai/lib: a
    module-scope import here would make every test's collection depend on
    agent_types importing cleanly.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    from agent_types import ENV_PREFIX
    return [k for k in os.environ if k.startswith(ENV_PREFIX)]


@pytest.fixture(autouse=True)
def _clear_agent_env():
    """Run every test with the agent config env unset.

    Model, thinking, and provider settings are read straight from the
    environment with no injection point, so a developer who exports
    WORKBENCH_AI_THINKING for their own runs answers those tests' assertions
    from their shell. Modules that resolve config guard themselves today; this
    is the floor, so the next one does not have to remember.

    Matching on the prefix rather than a list is what makes it a floor: the
    per-phase keys are generated from the Phase enum, so a new phase brings new
    keys that no list here would know about. Teardown drops whatever the test
    left behind before restoring, so a test that writes os.environ directly
    cannot leak into the next one either.
    """
    saved = {k: os.environ.pop(k) for k in _agent_env_keys()}
    yield
    for key in _agent_env_keys():
        del os.environ[key]
    os.environ.update(saved)


def _backend_binaries() -> set[str]:
    """The CLI names an AI call can spawn, from the enum that selects between them."""
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    from ai_backend import Backend
    return {b.value for b in Backend}


@pytest.fixture(autouse=True)
def _no_live_backend(monkeypatch):
    """Never let a test spawn a real agent CLI.

    The guard sits on ``subprocess`` rather than on ``ai_backend``, because the
    backend modules are where the spawn happens and a good many tests exercise
    them for real with ``subprocess.run`` stubbed. Replacing that function is
    how such a test declares it has taken the spawn over, and it replaces this
    wrapper along with it; a test that never stubs it reaches the wrapper and is
    refused.

    The floor exists because a missing stub does not read as one. A test that
    replaces ``<script>.ai_backend`` wholesale stopped covering the call once
    ``agent_invoke`` began holding its own reference to the module — the call
    then costs real money and answers differently every run, which surfaces as a
    flaky assertion rather than as an unstubbed seam.
    """
    binaries = _backend_binaries()
    real_run, real_popen = subprocess.run, subprocess.Popen

    def refuse_backend(cmd):
        argv0 = cmd[0] if isinstance(cmd, (list, tuple)) and cmd else cmd
        if Path(str(argv0)).name in binaries:
            raise AssertionError(
                f"a test spawned the {argv0} CLI for real — stub ai_backend's own "
                "attributes (the module agent_invoke holds), not a script's alias "
                "for it, or stub subprocess.run to answer as the CLI would"
            )

    def guarded_run(cmd, *args, **kwargs):
        refuse_backend(cmd)
        return real_run(cmd, *args, **kwargs)

    def guarded_popen(cmd, *args, **kwargs):
        refuse_backend(cmd)
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", guarded_run)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)


@pytest.fixture(autouse=True)
def _isolate_workbench_config(tmp_path, monkeypatch):
    """Run every test against an empty config root.

    ``workbench_config`` is layers 4 and 5 of the model, thinking, effort and
    issue-tracker chains, so a developer with a populated
    ``~/.config/workbench/config.yml`` would otherwise answer those tests'
    assertions from their own settings. Same floor as ``_clear_agent_env``,
    for the file half of the same precedence chain. Tests that want a config
    write one into this root.
    """
    monkeypatch.setenv("WORKBENCH_CONFIG_DIR", str(tmp_path / "workbench-config"))


@pytest.fixture(autouse=True)
def _isolate_installed_schema(monkeypatch):
    """Run every test on a machine with no workbench installed.

    ``check_key`` judges a config write against the schema of the *installed*
    workbench as well as this checkout's, which is the half that catches a
    stale writer. Unsandboxed, that makes the result depend on which commit
    ``~/.local/bin/otto-workbench`` points at — a developer whose install
    predates a key added here would watch this checkout's own tests refuse it.
    Hiding the launcher is how CI sees it too, and it leaves
    ``installed_schema_path`` itself real, so the tests covering resolution can
    say what they mean by patching the same lookup.

    Only the launcher is hidden. ``shutil.which`` answers for yq and for git on
    the paths under test, and a fixture that blanked those would be testing a
    machine nobody has.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import workbench_config_write

    real_which = shutil.which
    launcher = workbench_config_write.INSTALLED_LAUNCHER
    monkeypatch.setattr(workbench_config_write.shutil, "which", lambda name, *a, **kw: (
        None if name == launcher else real_which(name, *a, **kw)
    ))


@pytest.fixture(autouse=True)
def _clear_lock_env():
    """Never inherit a run lock marker across tests, or out of a real run.

    claim_for_process holds its handle until the process exits, which for a
    test process means the rest of the session — so drop those here too.
    Autouse for the same reason as _clear_agent_env: this is the floor, so
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

# `(section, key)` pairs of the same kind, for state that shares a section with
# user config so the section itself cannot be exempted:
#
#   `worktrunk.history` — the recently-used branch list `wt switch` rewrites,
#     which lands mid-run whenever any worktree of this repo switches. It sits
#     in `[worktrunk]` beside `default-branch`, which stays guarded.
_EXTERNAL_KEYS = ((b"worktrunk", b"history"),)


def _section_of(line: bytes) -> tuple[bytes, bytes] | None:
    """The `(section, subsection)` a `[header]` line opens, else None."""
    if not line.startswith(b"["):
        return None
    head, _, quoted = line[1:].partition(b'"')
    return head.strip(b"]").strip(), quoted.rsplit(b'"', 1)[0] if quoted else b""


def _is_external(section: bytes, subsection: bytes) -> bool:
    return any(section == name and subsection.startswith(prefix)
               for name, prefix in _EXTERNAL_STATE)


def _is_external_key(section: tuple[bytes, bytes], line: bytes) -> bool:
    """True for a value line naming a key that tooling outside this process owns.

    Only in a section with no subsection: the pairs name `[worktrunk]`, and a
    `[worktrunk "state.x"]` is already exempt as a whole.
    """
    name, subsection = section
    key = line.split(b"=", 1)[0].strip()
    return subsection == b"" and any(
        name == owner and key == owned for owner, owned in _EXTERNAL_KEYS
    )


def _without_empty_sections(lines: list[bytes]) -> list[bytes]:
    """The lines with headers that hold nothing dropped.

    A key exemption removes a value line but not the header above it, so an
    external write that opens a section — `wt switch` writing `history` into a
    repo with no `[worktrunk]` yet — would otherwise leave a bare header behind
    and read as a change. Nothing is lost: a leaked test is caught by the keys
    it writes, and a header with no keys says nothing on its own.
    """
    followed_by = [*lines[1:], b"["]
    return [line for line, following in zip(lines, followed_by)
            if _section_of(line.strip()) is None or _section_of(following.strip()) is None]


def _guarded_lines(raw: bytes | None) -> list[bytes] | None:
    """The config's lines with the externally-owned state dropped."""
    if raw is None:
        return None
    kept, section, external = [], (b"", b""), False
    for line in raw.splitlines():
        opened = _section_of(line.strip())
        if opened is not None:
            section, external = opened, _is_external(*opened)
        if not external and not _is_external_key(section, line):
            kept.append(line)
    return _without_empty_sections(kept)


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


def _restore_config(path: Path, before: bytes | None) -> None:
    """Put the snapshotted bytes back, so a caught leak is not also a repair job.

    Whole-file rather than a surgical undo of the offending keys: git rewrites
    the file wholesale, and reconstructing a partial merge would have to model
    multi-valued keys and includes to be safe. An external write that landed
    inside the same test's window is rolled back along with the leak — worktrunk
    restamps its markers on the next hook, and the alternative is leaving a
    poisoned identity in a config every worktree of the repo shares.
    """
    # ceiling: an unlocked write, so under `pytest -n` several workers can each
    # roll the file back to their own snapshot and the last one wins. Every
    # snapshot predates the leak, so the identity goes either way; what a losing
    # write can drop is an external marker that landed between two snapshots.
    # Upgrade to a lock held across snapshot-and-restore if the restored bytes
    # ever have to be exactly one worker's.
    if before is None:
        path.unlink(missing_ok=True)
        return
    path.write_bytes(before)


def _assert_config_unchanged(path: Path, before: bytes | None, after: bytes | None):
    """Restore `path` and raise unless every change to it is externally owned.

    The write is not necessarily the running test's. The snapshot is per test,
    so whatever lands in that window is what gets reported — which for a leak
    out of another process, or out of a subprocess that outlived the test that
    spawned it, names an arbitrary test. The message says so rather than
    accusing the one it interrupted.
    """
    if after == before:
        return
    guarded_before, guarded_after = _guarded_lines(before), _guarded_lines(after)
    if guarded_after == guarded_before:
        return
    _restore_config(path, before)
    raise AssertionError(
        f"git config of the repo under test changed mid-test: {path}\n"
        f"{_describe_config_change(guarded_before or [], guarded_after or [])}\n"
        f"The file has been restored. The writer is whatever ran during this "
        f"test, which need not be this test."
    )


@pytest.fixture(autouse=True)
def _guard_repo_config():
    """Catch and undo a write of git config into the repo under test.

    Tests build throwaway repos under tmp_path, but a stray GIT_DIR or a
    relative cwd sends `git config` to the real repo instead. Because worktrees
    share one config file, the damage would otherwise be repo-wide and
    permanent: every later commit inherits the test identity. The snapshot is
    what puts it back, so the failure is a report rather than a repair job.

    The state in `_EXTERNAL_STATE` and `_EXTERNAL_KEYS` is exempt: it is written
    concurrently by tooling this process does not control — worktrunk restamps
    its per-branch state whenever a session hook fires, and rewrites its branch
    history on any switch, mid-test-run included — and blaming the running test
    for those writes turns every long test run into a coin
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
REUSE_SESSION_START = REPO_ROOT / "ai" / "claude" / "bin" / "reuse-session-start"


def init_worktree(path) -> Path:
    """Make *path* a git worktree and return it.

    Per-worktree state lives in the worktree's own git dir, so a bare tmp_path
    is no longer a stand-in for a worktree — `git rev-parse` has to answer for
    it. No identity is configured: a test that commits sets its own, and the
    repo-config guard exists to catch the one that forgets.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    run_checked(["git", "init", "-b", "main", "-q", str(path)])
    return path


@pytest.fixture
def worktree(tmp_path) -> Path:
    """tmp_path as a git worktree, for tests that pass it as a worktree root."""
    return init_worktree(tmp_path)


GIT_TIMEOUT = 10  # seconds; a hang here should fail the test, not stall the suite


class MachineContention(AssertionError):
    """A subprocess a test drove died on a signal or a timeout.

    Its own class, and not a plain assertion, because the two failures a
    fixture's subprocess can hand back mean opposite things. A command that
    exits non-zero has diagnosed itself and the code under test is a suspect;
    a command killed by SIGPIPE, or cut off by a timeout, never got far enough
    to have an opinion, and the suspect is the machine it ran on.
    """


# Named so the reader stops looking for the bug in the diff. Three whole-suite
# runs at once on an 18-core machine reproduce this every time, in a handful of
# arbitrary tests that never repeat — a shape that reads as a real defect until
# somebody spends an afternoon proving otherwise.
_CONTENTION_HINT = (
    "This is a machine problem, not a test defect: the command never got far "
    "enough to report an error of its own. It is what an oversubscribed "
    "machine produces — a second full suite, a build, or several agents "
    "competing for the same cores. Re-run it on an idle machine, or lower "
    "TEST_JOBS, before treating it as a failure of the code under test."
)


def run_checked(argv, *, cwd=None, timeout=GIT_TIMEOUT, env=None):
    """Run *argv* to completion and return it, failing the test unless it exits 0.

    Output is captured as text, so the returned ``CompletedProcess`` carries
    ``stdout`` and ``stderr`` for a caller that wants them.

    The failure paths are what this exists for. A non-zero exit raises an
    ``AssertionError`` quoting the command's own stdout and stderr — a bare
    ``check=True`` renders as the exit code alone, which arrives without the
    message that says what broke. A death on an external signal (SIGKILL,
    SIGPIPE, ...) or a timeout raises ``MachineContention`` instead, which
    names the signal and says plainly that the machine, not the test, is the
    thing that failed. A fault signal (SIGSEGV, SIGABRT, ...) still raises
    ``AssertionError`` — that kind of death does point at the command, and
    ``ai/lib/proc.py``'s ``EXTERNAL_SIGNALS`` is the shared list of which
    signals get which treatment, so this stays in step with ``failure_message``
    without a second copy of the split.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import proc

    shown = " ".join(str(a) for a in argv)
    where = f" in {cwd}" if cwd else ""
    try:
        result = subprocess.run(
            argv, cwd=None if cwd is None else str(cwd), env=env,
            capture_output=True, text=True, timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise MachineContention(
            f"{shown} timed out after {timeout}s{where}\n{_CONTENTION_HINT}"
        ) from exc
    if result.returncode < 0 and -result.returncode in proc.EXTERNAL_SIGNALS:
        raise MachineContention(
            f"{shown} was killed by {proc.signal_description(result.returncode)}{where}\n"
            f"{_CONTENTION_HINT}"
        )
    if result.returncode < 0:
        raise AssertionError(
            f"{shown} was killed by {proc.signal_description(result.returncode)}{where}\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    if result.returncode != 0:
        raise AssertionError(
            f"{shown} failed (exit {result.returncode}){where}\n"
            f"stdout: {result.stdout.strip()}\nstderr: {result.stderr.strip()}"
        )
    return result


def git_out(cwd, *args, timeout=GIT_TIMEOUT) -> str:
    """Run git in *cwd* and return its stdout, failing the test on any error.

    The one git runner the suite's fixtures share. Per-module copies of it
    drifted into a dozen spellings of the same three lines, and none of them
    told a signal death apart from a git error — see ``run_checked``.
    """
    return run_checked(["git", "-C", str(cwd), *args], timeout=timeout).stdout


def git_in(cwd, *args) -> None:
    """Run a git command in *cwd*, failing the test if it errors or hangs."""
    git_out(cwd, *args)


def seed_repo(path) -> Path:
    """A one-commit repo at *path*, with an identity of its own.

    `init_worktree` above configures none, which is right for a test that never
    commits. This one does, so it passes `-c user.name`/`-c user.email` rather
    than writing them into a config the repo-config guard watches.
    """
    init_worktree(path)
    git_in(path, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty",
           "--no-verify", "-m", "init")
    return Path(path)


def init_repo(path) -> Path:
    """An empty repo at *path*, configured to commit without signing.

    For a test that drives a long sequence of git commands against a repo it
    owns, where `seed_repo`'s per-commit `-c` flags would be repeated on every
    one of them. The identity goes in the repo's own config, which is the
    throwaway `tmp_path` copy rather than anything the repo-config guard
    watches.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    git_in(path, "init", "-b", "main", "-q")
    git_in(path, "config", "user.email", "test@test.com")
    git_in(path, "config", "user.name", "Test")
    git_in(path, "config", "commit.gpgsign", "false")
    return path


def commit_all(path, message: str) -> None:
    """Stage everything in the repo at *path* and commit it, hooks skipped."""
    git_in(path, "add", ".")
    git_in(path, "commit", "-q", "--no-verify", "-m", message)


def add_self_origin(path) -> None:
    """Give the repo at *path* itself as `origin`, with `main` already fetched.

    Enough for code that resolves `origin/<base>` without a second repo to
    clone from.
    """
    git_in(path, "remote", "add", "origin", str(path))
    git_in(path, "fetch", "-q", "origin", "main")


@pytest.fixture
def container(tmp_path) -> Path:
    """The bare-repo worktree layout: worktrees as peers of a bare `.git`.

    What `wt-init` produces and what several checks have to reason about, since
    a `.claude/` written at the container sits above every worktree's walk. The
    returned directory holds the bare `.git` and a `main` worktree; add more
    with `add_worktree`.
    """
    seed = seed_repo(tmp_path / "seed")
    root = tmp_path / "container"
    run_checked(["git", "clone", "-q", "--bare", str(seed), str(root / ".git")])
    git_in(root / ".git", "worktree", "add", "-q", str(root / "main"), "main")
    return root


def add_worktree(container: Path, branch: str) -> Path:
    """A second worktree of the container's bare repo, on a new branch."""
    path = Path(container) / branch
    git_in(Path(container) / ".git", "worktree", "add", "-q", str(path), "-b", branch)
    return path


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


@pytest.fixture
def reviews_dir(tmp_path, monkeypatch) -> Path:
    """A throwaway reviews root, reached the way every workbench root is.

    Through the environment rather than by patching a path onto a module: the
    code under test resolves the root per call, so nothing here has to know
    which module reads it.
    """
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import workbench_paths
    d = workbench_paths.reviews_dir()
    d.mkdir(parents=True)
    return d


def seed_review(reviews_dir: Path, name: str = "widget-42",
                body: str | None = None, **meta) -> Path:
    """A review directory the reviews walk will classify as a review.

    One builder rather than one per suite, because the on-disk shape — a
    directory holding `review.md` beside a `meta.json` sidecar — is what the
    walk classifies on, and a second copy of it drifts the moment the layout
    moves. `meta.json` is written only when a caller passes attribution, so the
    default stands in for a review predating the sidecar.
    """
    d = reviews_dir / name
    d.mkdir()
    (d / "review.md").write_text(
        "## Must fix\n- **[M1]** a.py:1 — bug\n" if body is None else body,
    )
    if meta:
        (d / "meta.json").write_text(json.dumps(meta))
    return d


def supersession_verdict(*signals):
    """A canned verdict, for the callers that decide what to do about one.

    Shared because two entry points act on the same verdict in deliberately
    different ways — `pr comments` holds, `pr review` refuses — so both suites
    need to build one, and neither is testing detection.
    """
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    import supersession
    return supersession.Verdict(list(signals))


def supersession_evidence(detail="`foo` is gone from origin/main"):
    """A signal that argues the branch is superseded."""
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    from pr_domains import SupersessionKind, SupersessionSignal
    return SupersessionSignal(SupersessionKind.READDS_REMOVED_SYMBOL, detail)


def supersession_context(detail="replayed onto a moved base"):
    """A signal that explains the branch without arguing anything about it."""
    if LIB_DIR not in sys.path:
        sys.path.insert(0, LIB_DIR)
    from pr_domains import SupersessionKind, SupersessionSignal
    return SupersessionSignal(SupersessionKind.REBASE_SKEW, detail, holds=False)


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


def write_marker_file(directory, name: str, *lines: str) -> Path:
    """Write a ceiling-marker fixture, one line per argument.

    Never a triple-quoted block: the scanner reads every file in the repo, so a
    marker at the start of a line in a test file is a marker in the repo. Both
    the scanner's suite and the validator's need this, and the shape is the
    whole point of it — so it lives here rather than once per module.
    """
    path = Path(directory) / name
    path.write_text("\n".join(lines) + "\n")
    return path


# One module object per script, shared across every test that asks for it, so a
# test must not mutate module-level state. The fixtures are the ergonomic face
# of `load_script`; a module-level caller reaches for `load_script` directly.
@pytest.fixture(scope="session")
def rp():
    return load_script("review_post", REVIEW_POST)


@pytest.fixture(autouse=True)
def _isolate_state_root(tmp_path, monkeypatch):
    """Point the whole state root at a temp dir for the duration of every test.

    Every trail writer and the usage ledger append under this root, so an
    unsandboxed run would interleave test records with the developer's real
    history. The environment is set rather than an attribute patched because
    that is the only form a tool invoked as a subprocess inherits, and because
    every root resolves per call instead of freezing at import — one setenv
    therefore sandboxes every consumer, present and future.
    """
    monkeypatch.setenv("WORKBENCH_STATE_DIR", str(tmp_path / "state"))


@pytest.fixture(autouse=True)
def _disown_git_hooks(monkeypatch):
    """Detach every git command in every test from the machine's hooks.

    `core.hooksPath` is global on a workbench machine, so a repo created under
    `tmp_path` inherits the developer's real `pre-commit` — an identity
    assertion plus a `gitleaks` subprocess on each commit. The suite would then
    depend on machine state it does not own: the hook is free to change under
    it, a machine without gitleaks fails every commit in a temp repo, and under
    `pytest-xdist` each worker pays for its own scan.

    Set through git's own environment config rather than written into each repo,
    for the reason `_isolate_state_root` above sets an env var: a subprocess
    inherits it, so a repo built by a tool under test is covered too, and no new
    test has to remember to opt in. Git reads these as if they were `-c`, which
    outranks the repo-local `core.hooksPath` a few suites set for themselves —
    those point at empty directories to disable hooks, so /dev/null is what they
    were asking for anyway. A test that needs a hook to fire drops these keys.
    """
    monkeypatch.setenv("GIT_CONFIG_COUNT", "1")
    monkeypatch.setenv("GIT_CONFIG_KEY_0", "core.hooksPath")
    monkeypatch.setenv("GIT_CONFIG_VALUE_0", os.devnull)


@pytest.fixture
def live_git_hooks(monkeypatch):
    """Lift the hook sandbox, for a test whose subject is a hook firing.

    An opt-out has to remove the keys rather than write a louder value, since
    git reads them as `-c` and nothing a repo config says outranks that. The
    machine's global config goes with them, so what runs is the hook the test
    installed and never the developer's.
    """
    for key in ("GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_0"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


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
    """Clear get_bot_login lru_cache between tests."""
    yield
    if LIB_DIR in sys.path or "review_dedup" in sys.modules:
        try:
            import review_dedup
            review_dedup.get_bot_login.cache_clear()
        except (ImportError, AttributeError):
            pass


@pytest.fixture(scope="session")
def ro():
    return load_script("review_orchestrate", REVIEW_ORCHESTRATE)


@pytest.fixture(scope="session")
def rt():
    return load_script("review_threads", REVIEW_THREADS)


@pytest.fixture(scope="session")
def rss():
    return load_script("reuse_session_start", REUSE_SESSION_START)


@pytest.fixture(scope="session")
def em():
    return load_script("eval_models", EVAL_MODELS)


@pytest.fixture(scope="session")
def cc():
    return load_script("ci_check", CI_CHECK)


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
