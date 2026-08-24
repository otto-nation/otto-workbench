"""Tests for lib/permission_mirror.py — the grants written to a container.

The matcher, the coverage test, and the `_workbench` stamp are
`lib/permissions.py`' and are covered by tests/validate_permissions_test.py.
What is new here is the mirror's own three: choosing which worktree of a
container speaks for it, merging a generated file with whatever is already
there, and producing a file this repo's own gate accepts.
"""

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path

from conftest import add_worktree, git_in

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "lib"))

import permission_mirror as pm  # noqa: E402
import permissions as perms  # noqa: E402

_SCRIPT = REPO_ROOT / "bin" / "local" / "validate-permissions"
_loader = importlib.machinery.SourceFileLoader("validate_permissions_for_mirror", str(_SCRIPT))
_spec = importlib.util.spec_from_loader("validate_permissions_for_mirror", _loader)
vp = importlib.util.module_from_spec(_spec)
sys.modules["validate_permissions_for_mirror"] = vp
_spec.loader.exec_module(vp)

# The shape of a real tracked project file: a wildcard granting the repo's own
# scripts, and an ask rule gating the one that reaches credentials.
ALLOW = "Bash(bin/*)"
ASK = "Bash(bin/get-secret:*)"


def _tracked(worktree, allow=(ALLOW,), ask=(ASK,)):
    """Write a worktree's tracked .claude/settings.json and return its path."""
    path = Path(worktree) / perms.TRACKED_SETTINGS
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"permissions": {"allow": list(allow), "ask": list(ask)}}))
    return path


def _at(container):
    """The mirror file's path under a container."""
    return Path(container) / pm.MIRROR_SETTINGS


def _written(container):
    """The mirror the container holds, parsed."""
    return json.loads(_at(container).read_text())


def _run(container, *, dry_run=False):
    """Mirror the container from its `main` worktree, and return the one Result."""
    results = pm.mirror([str(Path(container) / "main")], dry_run)
    assert len(results) == 1
    return results[0]


# ── choosing the source worktree ─────────────────────────────────────────


def test_the_source_is_the_worktree_on_the_branch_head_names(container):
    feature = add_worktree(container, "feat")
    found = pm.source_of(str(container), [str(feature), str(container / "main")])
    assert found == str(container / "main")


def test_a_container_no_registered_worktree_speaks_for_is_skipped(container):
    """Mirroring from whichever branch the registry listed first is the bug."""
    feature = add_worktree(container, "feat")
    _tracked(feature)
    results = pm.mirror([str(feature)])
    assert [(r.ok, r.skipped) for r in results] == [(False, pm.NO_SOURCE)]
    assert not _at(container).exists()


def test_a_container_with_nothing_to_receive_is_not_even_reported(container):
    """Most registered repos keep no tracked grants; none of them is a finding."""
    add_worktree(container, "feat")
    assert pm.mirror([str(container / "feat")]) == []


def test_an_existing_mirror_is_a_stake_even_with_no_tracked_grants(container):
    """Its managed rules may now need emptying, which needs a source to say so."""
    _at(container).parent.mkdir(parents=True)
    _at(container).write_text(json.dumps({"permissions": {}}))
    add_worktree(container, "feat")
    assert [r.skipped for r in pm.mirror([str(container / "feat")])] == [pm.NO_SOURCE]


def test_a_plain_clone_has_no_container_to_mirror(tmp_path):
    """Its tracked file already applies — a session there is rooted in it."""
    from conftest import seed_repo

    assert pm.mirror([str(seed_repo(tmp_path / "clone"))]) == []


def test_sibling_worktrees_produce_one_mirror(container):
    add_worktree(container, "feat")
    repos = [str(container / "feat"), str(container / "main")]
    _tracked(container / "main")
    assert len(pm.mirror(repos)) == 1


# ── what the mirror holds ────────────────────────────────────────────────


def test_both_buckets_travel(container):
    """An allow arriving without its gate is worse than the prompt it removes."""
    _tracked(container / "main")
    _run(container)
    assert _written(container)["permissions"] == {"allow": [ALLOW], "ask": [ASK]}


def test_the_mirror_records_the_file_it_was_copied_from(container):
    tracked = _tracked(container / "main")
    _run(container)
    assert _written(container)[perms.MANIFEST_KEY]["source"] == str(tracked)


def test_the_stamp_claims_exactly_what_was_written(container):
    _tracked(container / "main")
    _run(container)
    stamp = _written(container)[perms.MANIFEST_KEY]["permissions"]
    assert stamp == {"allow": [ALLOW], "ask": [ASK]}


def test_a_repeat_run_changes_nothing(container):
    _tracked(container / "main")
    assert _run(container).changed is True
    before = _at(container).read_text()
    assert _run(container).changed is False
    assert _at(container).read_text() == before


def test_a_dry_run_writes_no_file(container):
    _tracked(container / "main")
    assert _run(container, dry_run=True).changed is True
    assert not _at(container).exists()


def test_a_repo_with_no_tracked_grants_writes_nothing(container):
    """Most repos keep none, so an empty mirror everywhere would be all output."""
    result = _run(container)
    assert result.ok and not result.changed
    assert not _at(container).exists()


# ── merging with what is already there ───────────────────────────────────


def test_a_grant_the_user_approved_at_the_container_survives(container):
    """It may be the only record of a command no tracked rule speaks for."""
    _at(container).parent.mkdir(parents=True)
    _at(container).write_text(json.dumps({"permissions": {"allow": ["Bash(kubectl get:*)"]}}))
    _tracked(container / "main")
    _run(container)
    assert _written(container)["permissions"]["allow"] == [ALLOW, "Bash(kubectl get:*)"]


def test_a_rule_the_previous_stamp_claimed_is_dropped(container):
    """Deleting a tracked grant has to reach the mirrors it was copied into."""
    _tracked(container / "main", allow=[ALLOW, "Bash(git/bin/*)"])
    _run(container)
    _tracked(container / "main", allow=[ALLOW])
    _run(container)
    assert _written(container)["permissions"]["allow"] == [ALLOW]


def test_a_key_the_mirror_does_not_own_is_left_alone(container):
    _at(container).parent.mkdir(parents=True)
    _at(container).write_text(json.dumps({"model": "opus", "permissions": {}}))
    _tracked(container / "main")
    _run(container)
    assert _written(container)["model"] == "opus"


def test_an_unparsable_file_at_the_container_is_left_alone(container):
    """It may hold approved grants, and only prompts are on the other side."""
    _at(container).parent.mkdir(parents=True)
    _at(container).write_text("{ not json")
    _tracked(container / "main")

    result = _run(container)
    assert not result.ok
    assert _at(container).read_text() == "{ not json"


def test_a_user_entry_duplicating_a_managed_one_is_not_written_twice(container):
    _at(container).parent.mkdir(parents=True)
    _at(container).write_text(json.dumps({"permissions": {"allow": [ALLOW]}}))
    _tracked(container / "main")
    _run(container)
    assert _written(container)["permissions"]["allow"] == [ALLOW]


# ── the gate accepts what the mirror writes ──────────────────────────────


def test_the_mirror_passes_this_repos_own_validator(container, monkeypatch, capsys):
    """The end this exists for: a real mirror, then the gate that runs pre-push.

    Every rule in the mirror duplicates the tracked file — that is the point —
    so a mirror the drift check does not recognise fails validate-all on the
    fix itself.
    """
    worktree = container / "main"
    _tracked(worktree)
    assert _run(container).changed is True

    monkeypatch.setattr(vp, "_WORKBENCH_DIR", str(worktree))
    monkeypatch.setattr(sys, "argv", ["validate-permissions"])
    vp.main()

    assert "every permission rule is live" in capsys.readouterr().out


def test_the_mirror_is_not_written_into_the_worktree(container):
    """A file inside a worktree would be untracked and die with the checkout."""
    _tracked(container / "main")
    _run(container)
    assert not (container / "main" / ".claude" / "settings.local.json").exists()
    assert _at(container).exists()


# ── reading the container's HEAD ─────────────────────────────────────────


def test_head_branch_reads_the_bare_repos_default(container):
    assert pm.head_branch(str(container)) == "main"


def test_head_branch_follows_a_moved_head(container):
    add_worktree(container, "feat")
    git_in(container / ".git", "symbolic-ref", "HEAD", "refs/heads/feat")
    assert pm.head_branch(str(container)) == "feat"
    assert pm.source_of(str(container), [str(container / "feat")]) == str(container / "feat")


def test_a_directory_git_cannot_answer_for_has_no_head(tmp_path):
    assert pm.head_branch(str(tmp_path)) is None
