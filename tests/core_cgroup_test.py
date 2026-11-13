"""This process's cgroup v2 path, and the user manager it sits under."""

from pathlib import Path, PurePosixPath

import core.cgroup

USER_SCOPE = "/user.slice/user-1000.slice/user@1000.service/app.slice/otto.scope"


def _self_cgroup(root: Path, path: str) -> None:
    (root / "proc" / "self").mkdir(parents=True)
    (root / "proc" / "self" / "cgroup").write_text(f"0::{path}\n")


def test_own_cgroup_reads_the_v2_line(tmp_path):
    _self_cgroup(tmp_path, USER_SCOPE)
    assert core.cgroup.own_cgroup(tmp_path) == PurePosixPath(USER_SCOPE)


def test_own_cgroup_without_a_v2_line_is_root(tmp_path):
    (tmp_path / "proc" / "self").mkdir(parents=True)
    (tmp_path / "proc" / "self" / "cgroup").write_text("1:name=systemd:/\n")
    assert core.cgroup.own_cgroup(tmp_path) == PurePosixPath("/")


def test_user_manager_cgroup_is_the_user_service_prefix(tmp_path):
    _self_cgroup(tmp_path, USER_SCOPE)
    assert core.cgroup.user_manager_cgroup(tmp_path) == PurePosixPath(
        "/user.slice/user-1000.slice/user@1000.service")


def test_user_manager_cgroup_outside_a_user_manager_is_none(tmp_path):
    _self_cgroup(tmp_path, "/user.slice/user-1000.slice/session-3.scope")
    assert core.cgroup.user_manager_cgroup(tmp_path, uid=1000) is None


def test_user_manager_cgroup_from_a_login_session_is_the_conventional_path(tmp_path):
    _self_cgroup(tmp_path, "/user.slice/user-1000.slice/session-3.scope")
    (tmp_path / "sys/fs/cgroup/user.slice/user-1000.slice/user@1000.service").mkdir(parents=True)
    assert core.cgroup.user_manager_cgroup(tmp_path, uid=1000) == PurePosixPath(
        "/user.slice/user-1000.slice/user@1000.service")
