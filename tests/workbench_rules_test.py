"""Domain aliases and the instructions-resolver failure path for workbench-rules.

The CLI is Python, so these used to source bash functions from the script.
`tests/workbench_rules.bats` still drives the binary for add/list/status/sync
and project add/show.
"""

import subprocess
import sys
from pathlib import Path

LIB_DIR = Path(__file__).resolve().parent.parent / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.workbench_rules  # noqa: E402
import core.project_context  # noqa: E402
import core.workbench_rules  # noqa: E402
from core.exit_codes import ExitCode  # noqa: E402


def test_normalize_domain_ts_maps_to_typescript():
    assert core.workbench_rules.normalize_domain("ts") == "typescript"


def test_normalize_domain_js_maps_to_typescript():
    assert core.workbench_rules.normalize_domain("js") == "typescript"


def test_normalize_domain_py_maps_to_python():
    assert core.workbench_rules.normalize_domain("py") == "python"


def test_normalize_domain_sh_maps_to_bash():
    assert core.workbench_rules.normalize_domain("sh") == "bash"


def test_normalize_domain_shell_maps_to_bash():
    assert core.workbench_rules.normalize_domain("shell") == "bash"


def test_normalize_domain_yml_maps_to_yaml():
    assert core.workbench_rules.normalize_domain("yml") == "yaml"


def test_normalize_domain_unknown_domain_passes_through():
    assert core.workbench_rules.normalize_domain("go") == "go"


def test_normalize_domain_custom_domain_passes_through():
    assert core.workbench_rules.normalize_domain("kubernetes") == "kubernetes"


def _git_repo(root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(
        ["git", "-C", str(root), "config", "user.email", "test@example.com"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "config", "user.name", "Test"],
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(root), "commit", "-q", "--allow-empty", "-m", "init"],
        check=True,
    )


def test_project_add_reports_a_resolve_failure_distinctly_from_no_file(
        tmp_path, monkeypatch, capsys):
    repo = tmp_path / "myrepo"
    repo.mkdir()
    _git_repo(repo)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("NO_COLOR", "1")

    def boom(_root):
        raise OSError("resolver failed")

    monkeypatch.setattr(core.project_context, "resolve", boom)
    status = cli.workbench_rules.main(["project", "add", "some rule"])
    captured = capsys.readouterr()
    output = captured.out + captured.err
    assert status == ExitCode.FAILURE
    assert "No AGENTS.md" not in output
    assert "Could not determine the instructions file" in output
