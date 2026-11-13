"""This process's cgroup v2 path, and the user manager it sits under.

`/proc/self/cgroup` is the one place the kernel writes that path. Admission
walks it for `workbench.slice`; the queue worker walks it to check memory
delegation. Two copies of the walk would drift, so both read here.
"""

# doc-group: platform

from __future__ import annotations

import os
import re
from pathlib import Path, PurePosixPath

# The user manager's own unit in a cgroup path. Every user slice, workbench.slice
# among them, is its child.
_USER_MANAGER = re.compile(r"user@\d+\.service")


def own_cgroup(root: Path = Path("/")) -> PurePosixPath:
    """This process's cgroup v2 path, from `/proc/self/cgroup`; `/` when there is none.

    Only the unified hierarchy's `0::` line is read. A v1-only host has no
    `memory.max` to find, and `/` keeps it on the root files as before.
    """
    try:
        text = (root / "proc/self/cgroup").read_text()
    except OSError:
        text = ""
    for line in text.splitlines():
        if line.startswith("0::"):
            return PurePosixPath(line[3:].strip() or "/")
    return PurePosixPath("/")


def user_manager_cgroup(root: Path = Path("/"), uid: int | None = None) -> PurePosixPath | None:
    """The user@N.service cgroup of this user's manager; None when there is none.

    Read off this process's own path first. A process outside the manager — a
    login shell's `session-N.scope`, cron — has no such part in its path, yet
    the manager and the slices under it still exist; for those the conventional
    `user.slice/user-<uid>.slice/user@<uid>.service` is used when it is there.
    """
    parts = own_cgroup(root).parts
    for depth, part in enumerate(parts):
        if _USER_MANAGER.fullmatch(part):
            return PurePosixPath(*parts[:depth + 1])
    uid = os.getuid() if uid is None else uid
    conventional = PurePosixPath("/user.slice", f"user-{uid}.slice", f"user@{uid}.service")
    if (root / "sys/fs/cgroup").joinpath(*conventional.parts[1:]).is_dir():
        return conventional
    return None
