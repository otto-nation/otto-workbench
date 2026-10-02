"""Tests for wiki snapshots: taking, listing, pruning and restoring a backup."""

import json
import shutil
import sys
import tarfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_DIR = REPO_ROOT / "ai" / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

import cli.wiki  # noqa: E402

from wiki_support import make_wiki, write_article


class TestBackup:
    """Snapshots of the one tree nothing can regenerate."""

    def _base(self, tmp_path: Path) -> Path:
        root = make_wiki(tmp_path)
        write_article(root, "kept", body="word " * 20, tags=["x"])
        (root / "raw" / "source.md").write_text("the source\n", encoding="utf-8")
        return root

    def test_a_snapshot_lands_under_the_state_root(self, tmp_path, monkeypatch):
        """State, not data: a snapshot has a producer, and a base does not."""
        state = tmp_path / "state"
        data = tmp_path / "data"
        monkeypatch.setenv("WORKBENCH_STATE_DIR", str(state))
        monkeypatch.setenv("WORKBENCH_DATA_DIR", str(data))
        root = self._base(tmp_path)
        archive = cli.wiki.snapshot(root)
        assert state in archive.parents
        assert data not in archive.parents

    def test_the_snapshot_holds_what_the_base_held(self, tmp_path):
        root = self._base(tmp_path)
        archive = cli.wiki.snapshot(root)
        with tarfile.open(archive, "r:gz") as tar:
            names = tar.getnames()
        assert f"{root.name}/SCHEMA.md" in names
        assert f"{root.name}/articles/kept.md" in names
        assert f"{root.name}/raw/source.md" in names

    def test_restore_brings_back_a_readable_base(self, tmp_path):
        """The restore path, exercised rather than assumed."""
        root = self._base(tmp_path)
        original = (root / "raw" / "source.md").read_bytes()
        archive = cli.wiki.snapshot(root)
        shutil.rmtree(root / "raw")
        (root / "raw").mkdir()

        landed = cli.wiki.restore(root, archive)
        assert cli.wiki.is_wiki(landed)
        assert (landed / "raw" / "source.md").read_bytes() == original

    def test_restore_leaves_the_live_base_alone(self, tmp_path):
        """A restore runs after something went wrong; it must not cause another."""
        root = self._base(tmp_path)
        archive = cli.wiki.snapshot(root)
        (root / "articles" / "kept.md").write_text("newer", encoding="utf-8")
        landed = cli.wiki.restore(root, archive)
        assert landed != root
        assert (root / "articles" / "kept.md").read_text(encoding="utf-8") == "newer"

    def test_retention_keeps_the_newest_and_drops_the_rest(self, tmp_path):
        root = self._base(tmp_path)
        directory = cli.wiki.backups_dir(root)
        directory.mkdir(parents=True)
        for day in range(1, 6):
            (directory / f"2026010{day}T000000Z.tar.gz").write_bytes(b"old")
        cli.wiki.prune(root, keep=2)
        survivors = [a.name for a in cli.wiki.snapshots(root)]
        assert survivors == ["20260104T000000Z.tar.gz", "20260105T000000Z.tar.gz"]

    def test_an_interrupted_snapshot_leaves_nothing_behind(self, tmp_path, monkeypatch):
        """A truncated archive would restore to a partial base without saying so."""
        root = self._base(tmp_path)

        def explode(*_args, **_kwargs):
            raise OSError("disk full")

        # Patches the `tarfile` module directly, imported by this file, rather than
        # reaching it through `wiki.wiki.backup.tarfile` — two levels of indirection
        # that would break silently if `backup.py`'s import structure changed.
        monkeypatch.setattr(tarfile, "open", explode)
        with pytest.raises(OSError):
            cli.wiki.snapshot(root)
        assert cli.wiki.snapshots(root) == []
        assert list(cli.wiki.backups_dir(root).glob("*")) == []

    def test_two_snapshots_in_one_second_are_both_kept(self, tmp_path):
        """The stamp is per-second, so the second would otherwise overwrite the first."""
        root = self._base(tmp_path)
        when = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        first = cli.wiki.snapshot(root, now=when)
        second = cli.wiki.snapshot(root, now=when)
        assert first != second
        assert len(cli.wiki.snapshots(root)) == 2

    def test_retention_within_one_second_drops_the_earlier_snapshot(self, tmp_path):
        """Retention deletes from the front, so a wrong order deletes the newest.

        Asserted on *which file survives*, not on the list being sorted:
        `snapshots` sorts on the way out, so comparing it against `sorted()` is
        a tautology that passes whatever the names are. Names are ordered as
        text, and an unsuffixed name sorts after its own `-2` sibling — so with
        two snapshots in one second, the one pruned was the later of the two.
        """
        root = self._base(tmp_path)
        when = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        first = cli.wiki.snapshot(root, now=when)
        second = cli.wiki.snapshot(root, now=when)
        first.write_bytes(b"earlier")
        second.write_bytes(b"later")

        cli.wiki.prune(root, keep=1)
        survivors = cli.wiki.snapshots(root)
        assert len(survivors) == 1
        assert survivors[0].read_bytes() == b"later"

    def test_a_same_second_snapshot_still_carries_its_date(self, tmp_path):
        """An unparsed stamp would read as never-backed-up and always prompt."""
        root = self._base(tmp_path)
        when = datetime(2026, 5, 1, 12, 0, 0, tzinfo=timezone.utc)
        cli.wiki.snapshot(root, now=when)
        cli.wiki.snapshot(root, now=when)
        assert not cli.wiki.is_overdue(root, now=datetime(2026, 5, 2, tzinfo=timezone.utc))

    def test_restoring_twice_in_one_second_does_not_collide(self, tmp_path):
        """The second restore raised FileExistsError at the user rather than landing."""
        root = self._base(tmp_path)
        archive = cli.wiki.snapshot(root)
        first = cli.wiki.restore(root, archive)
        second = cli.wiki.restore(root, archive)
        assert first != second
        assert cli.wiki.is_wiki(first) and cli.wiki.is_wiki(second)

    def test_two_bases_with_one_name_do_not_share_a_directory(self, tmp_path):
        first = make_wiki(tmp_path / "one", dirname="notes")
        second = make_wiki(tmp_path / "two", dirname="notes")
        assert cli.wiki.backups_dir(first) != cli.wiki.backups_dir(second)

    def test_the_backup_directory_is_the_same_on_every_call(self, tmp_path):
        """A process-randomised hash would send one base to a new directory a run."""
        root = self._base(tmp_path)
        assert cli.wiki.backups_dir(root) == cli.wiki.backups_dir(root)

    def test_reaching_a_base_through_a_symlink_gets_the_same_directory(self, tmp_path):
        """A caller that skips `.resolve()` must not land on a second directory.

        Through a symlink, because that is the spelling `.resolve()` is actually
        for: pathlib collapses `.` and `..` when the path is built, so a dotted
        spelling is already identical before anything resolves it and would pass
        this whether `backups_dir` resolved or not.
        """
        root = self._base(tmp_path)
        link = tmp_path / "link-to-wiki"
        link.symlink_to(root, target_is_directory=True)
        assert cli.wiki.backups_dir(link) == cli.wiki.backups_dir(root)

    def test_a_base_with_no_snapshot_is_overdue(self, tmp_path):
        assert cli.wiki.is_overdue(self._base(tmp_path))

    def test_a_freshly_snapshotted_base_is_not_overdue(self, tmp_path):
        root = self._base(tmp_path)
        cli.wiki.snapshot(root)
        assert not cli.wiki.is_overdue(root)

    def test_an_old_snapshot_is_overdue_again(self, tmp_path):
        root = self._base(tmp_path)
        cli.wiki.snapshot(root, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        assert cli.wiki.is_overdue(root, now=datetime(2026, 6, 1, tzinfo=timezone.utc))

    def test_status_prompts_when_a_base_has_never_been_snapshotted(self, tmp_path, capsys):
        root = self._base(tmp_path)
        assert cli.wiki.main(["status", str(root)]) == 0
        assert "wiki backup" in capsys.readouterr().out

    def test_status_stops_prompting_once_a_snapshot_exists(self, tmp_path, capsys):
        root = self._base(tmp_path)
        cli.wiki.snapshot(root)
        assert cli.wiki.main(["status", str(root)]) == 0
        assert "wiki backup" not in capsys.readouterr().out

    def test_status_json_carries_no_backup_key(self, tmp_path, capsys):
        root = self._base(tmp_path)
        assert cli.wiki.main(["status", "--json", str(root)]) == 0
        assert set(json.loads(capsys.readouterr().out)) == set(cli.wiki.collect_status(cli.wiki.Wiki(root)))

    def test_restoring_by_name_picks_that_snapshot(self, tmp_path, capsys):
        root = self._base(tmp_path)
        first = cli.wiki.snapshot(root, now=datetime(2026, 1, 1, tzinfo=timezone.utc))
        cli.wiki.snapshot(root, now=datetime(2026, 2, 1, tzinfo=timezone.utc))
        assert cli.wiki.main(["backup", "--restore", first.name, str(root)]) == 0
        assert first.name in capsys.readouterr().out

    def test_restoring_an_unknown_name_is_refused(self, tmp_path, capsys):
        root = self._base(tmp_path)
        cli.wiki.snapshot(root)
        assert cli.wiki.main(["backup", "--restore", "nosuch.tar.gz", str(root)]) == 1
        assert "no snapshot named" in capsys.readouterr().err
