"""The repo-config guard's parsing: which changes to the shared git config are a leak.

`conftest.py` holds the autouse fixture that snapshots the config of the repo
under test around every test; this module holds the reading behind it — which
sections and keys tooling outside the test process owns, and how a change is
described and undone. It lives apart from the fixture so `conftest_guards_test`
can drive it directly, and so conftest carries the fixtures rather than the
parser.
"""

import difflib
from pathlib import Path

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
