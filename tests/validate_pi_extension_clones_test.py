"""Tests for bin/local/validate-pi-extension-clones.

The validator's whole value is the truth table between host version and clone
contents, so that is what these pin. Both skew directions have really happened
on this machine and each fails differently:

  stale clone + new host  — silent. The provider sends no tools, the model
                            fabricates their output, the run exits 0.
  fresh clone + old host  — loud. TypeError on turn 1, zero tool calls.

A validator that caught only the loud one would have reported green through
the entire fabrication window, which is the case that prompted it.
"""

import json
import subprocess
from pathlib import Path

import pytest

from conftest import load_script

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / 'bin' / 'local' / 'validate-pi-extension-clones'

vpec = load_script('validate_pi_extension_clones', SCRIPT)

# Versions either side of the transcript contract, named so a reader does not
# have to remember which release moved it.
HOST_WITH_TRANSCRIPT = (0, 87, 1)
HOST_BEFORE_TRANSCRIPT = (0, 84, 4)


@pytest.fixture
def validator():
    return vpec


def _clone(root: Path, *, provider: bool, adapter: bool) -> Path:
    """A package clone with the given shape, as a real git repo."""
    path = root / '.pi' / 'git' / 'github.com' / 'usemaximum' / 'pi-extensions'
    ext = path / 'extensions' / 'vertex-claude'
    ext.mkdir(parents=True)
    if provider:
        (ext / 'index.ts').write_text(
            'export function activate(ctx) { ctx.registerProvider({ id: "vertex" }); }\n')
    else:
        (ext / 'index.ts').write_text('export const tools = [];\n')
    if adapter:
        (ext / 'transcript.ts').write_text(
            'import { getCurrentSystemPrompt, getCurrentTools } from "@mariozechner/pi-ai";\n')
    subprocess.run(['git', 'init', '-q', str(path)], check=True)
    return path


def test_stale_provider_clone_on_a_new_host_is_flagged(validator, tmp_path):
    """The silent case: no adapter, host past 0.86. Tools vanish, output is invented."""
    clone = _clone(tmp_path, provider=True, adapter=False)
    problem = validator.check_clone(clone, HOST_WITH_TRANSCRIPT)
    assert problem is not None
    assert 'predates' in problem


def test_a_stale_clone_warns_that_results_may_be_invented(validator, tmp_path):
    """The message has to name the fabrication, not just the version skew.

    'Your clone is old' reads as housekeeping and gets deferred. The reason to
    stop is that a green run may be reporting work it never did.
    """
    clone = _clone(tmp_path, provider=True, adapter=False)
    problem = validator.check_clone(clone, HOST_WITH_TRANSCRIPT)
    assert 'never produced' in problem


def test_fresh_provider_clone_on_an_old_host_is_flagged(validator, tmp_path):
    """The loud case: adapter present, host below 0.86. Every agent dies turn 1."""
    clone = _clone(tmp_path, provider=True, adapter=True)
    problem = validator.check_clone(clone, HOST_BEFORE_TRANSCRIPT)
    assert problem is not None
    assert 'older' in problem


def test_matched_clone_and_host_pass(validator, tmp_path):
    clone = _clone(tmp_path, provider=True, adapter=True)
    assert validator.check_clone(clone, HOST_WITH_TRANSCRIPT) is None


def test_matched_old_clone_and_old_host_pass(validator, tmp_path):
    clone = _clone(tmp_path, provider=True, adapter=False)
    assert validator.check_clone(clone, HOST_BEFORE_TRANSCRIPT) is None


def test_a_package_with_no_provider_is_not_judged(validator, tmp_path):
    """A tools-only package has no stake in the transcript contract.

    Without this the check would fail every unrelated package — superpowers
    among them — and a validator that cries wolf on a clone it has nothing to
    say about is one people switch off.
    """
    clone = _clone(tmp_path, provider=False, adapter=False)
    assert validator.check_clone(clone, HOST_WITH_TRANSCRIPT) is None


def test_node_modules_is_not_searched_for_the_adapter(validator, tmp_path):
    """A dependency mentioning the symbol must not stand in for the adapter.

    The clone's own node_modules carries pi-ai, which defines the very symbols
    being looked for — counting those would mark every stale clone fresh and
    silently disable the check.
    """
    clone = _clone(tmp_path, provider=True, adapter=False)
    vendored = clone / 'node_modules' / '@mariozechner' / 'pi-ai'
    vendored.mkdir(parents=True)
    (vendored / 'index.ts').write_text('export function getCurrentSystemPrompt() {}\n')

    assert validator._declares_transcript_adapter(clone) is False
    assert validator.check_clone(clone, HOST_WITH_TRANSCRIPT) is not None


def test_discovery_finds_a_user_scope_clone(validator, tmp_path, monkeypatch):
    """User scope is the one that serves every session with no project package.

    It was missed once already: deleting the project clones left this one stale
    and the machine still fabricating, because nothing walks it by default.
    """
    root = tmp_path / 'agent' / 'git'
    pkg = root / 'github.com' / 'usemaximum' / 'pi-extensions'
    pkg.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', str(pkg)], check=True)

    monkeypatch.setattr(validator, 'USER_PACKAGE_ROOT', root)
    monkeypatch.setattr(validator.workbench_projects, 'registered', lambda: [])

    found = validator.discover_clones()
    assert [('user', pkg)] == found


def test_discovery_finds_a_deeply_nested_registered_project(validator, tmp_path, monkeypatch):
    """A registered project's `.pi/git` is found at whatever depth it lives.

    The registry records worktree roots directly, so this has nothing to guess
    at: a repo four levels below a container is exactly as reachable as one at
    the top, which a guessed root plus a depth cap could not promise.
    """
    project_root = tmp_path / 'git' / 'personal' / 'otto-nation' / 'otto-workbench' / 'main'
    pkg = project_root / '.pi' / 'git' / 'github.com' / 'usemaximum' / 'pi-extensions'
    pkg.mkdir(parents=True)
    subprocess.run(['git', 'init', '-q', str(pkg)], check=True)

    monkeypatch.setattr(validator, 'USER_PACKAGE_ROOT', tmp_path / 'agent' / 'git')
    monkeypatch.setattr(validator.workbench_projects, 'registered', lambda: [project_root])

    found = validator.discover_clones()
    assert [('project', pkg)] == found


def test_version_parsing_reads_a_bare_version_line(validator, monkeypatch):
    monkeypatch.setattr(validator, '_run', lambda *a, **k: '0.87.1')
    assert validator.installed_pi_version() == (0, 87, 1)


def test_version_parsing_rejects_a_prefixed_banner(validator, monkeypatch):
    """An unanchored match on the first number sequence would misreport this as
    (1, 0, 0) instead of failing loudly on a banner shape this has never seen.
    """
    monkeypatch.setattr(validator, '_run',
                        lambda *a, **k: 'update available: 1.0.0 (installed 0.87.1)')
    assert validator.installed_pi_version() is None


def test_no_pi_installed_is_not_a_violation(validator, monkeypatch):
    """The gate validates an installation; it has no opinion without one."""
    monkeypatch.setattr(validator, '_run', lambda *a, **k: None)
    assert validator.installed_pi_version() is None


def test_json_output_when_pi_is_not_installed(validator, monkeypatch, capsys):
    """--json is documented as always emitting JSON, absent pi included."""
    monkeypatch.setattr(validator, 'installed_pi_version', lambda: None)
    monkeypatch.setattr(validator.sys, 'argv', ['validate-pi-extension-clones', '--json'])
    validator.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload == {'pi_version': None, 'clones_checked': 0, 'findings': []}


def test_json_output_when_no_clones_found(validator, monkeypatch, capsys):
    """--json is documented as always emitting JSON, no clones included."""
    monkeypatch.setattr(validator, 'installed_pi_version', lambda: (0, 87, 1))
    monkeypatch.setattr(validator, 'discover_clones', lambda: [])
    monkeypatch.setattr(validator.sys, 'argv', ['validate-pi-extension-clones', '--json'])
    validator.main()
    payload = json.loads(capsys.readouterr().out)
    assert payload == {'pi_version': '0.87.1', 'clones_checked': 0, 'findings': []}
