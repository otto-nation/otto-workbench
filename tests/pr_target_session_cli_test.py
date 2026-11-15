"""`pr target session`: print the target-session recipe, or exec pi with it.

Revert: build the exec argv without `--session-id`; the exec test fails.
"""

import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
BIN_DIR = REPO_ROOT / "ai" / "bin"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import batch.target_session  # noqa: E402
import cli.pr  # noqa: E402
import cli.schema  # noqa: E402
import core.trail  # noqa: E402
import pr.target  # noqa: E402
from pr_target_support import make_target  # noqa: E402


def _main(argv):
    try:
        return cli.pr.main(list(argv), bin_dir=BIN_DIR)
    except SystemExit as exc:
        return exc.code


def test_session_json_prints_the_recipe(tmp_path, capsys):
    t = make_target(tmp_path)
    assert _main(["--repo-dir", str(t.repo), "target", "session", "--json"]) == 0
    doc = json.loads(capsys.readouterr().out)
    sid = batch.target_session.session_id(t.target_dir)
    label = pr.target.repo_identity_from_origin(str(t.repo)).label
    assert doc["session_id"] == sid
    assert doc["name"] == f"target {label} feat/x"
    assert Path(doc["cwd"]).resolve() == t.repo.resolve()
    assert doc["argv"] == ["pi", "--session-id", sid, "--name", f"target {label} feat/x"]
    assert doc["env"] == {"WORKBENCH_TARGET_DIR": str(t.target_dir), "WORKBENCH_SESSION_ID": sid}
    assert doc["schema_version"] == 1


def test_session_without_json_execs_pi_after_finishing_the_trail(tmp_path, monkeypatch):
    t = make_target(tmp_path)
    calls = {"finished": 0}
    real_finish = core.trail.Trail.finish

    def finish(self):
        calls["finished"] += 1
        real_finish(self)

    monkeypatch.setattr(core.trail.Trail, "finish", finish)
    monkeypatch.setattr(os, "chdir", lambda path: calls.setdefault("cwd", path))

    def fake_exec(file, argv, env):
        calls.update(file=file, argv=argv, env=env, finished_before_exec=calls["finished"])
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_exec)
    assert _main(["--repo-dir", str(t.repo), "target", "session"]) == 0
    sid = batch.target_session.session_id(t.target_dir)
    assert Path(calls["cwd"]).resolve() == t.repo.resolve()
    assert calls["file"] == "pi"
    assert calls["argv"][:3] == ["pi", "--session-id", sid]
    assert calls["env"]["WORKBENCH_SESSION_ID"] == sid
    assert calls["env"]["PATH"] == os.environ["PATH"]
    assert calls["finished_before_exec"] == 1


def test_session_serves_schema_version_1():
    assert cli.schema.served_schema_versions("target", ["session"]) == (1,)
