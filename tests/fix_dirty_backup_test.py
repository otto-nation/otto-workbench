"""Pre-pass copies of already-dirty files, byte-exact and never aborting."""

import json
import sys
from pathlib import Path
from unittest.mock import Mock, patch

LIB_DIR = str(Path(__file__).resolve().parent.parent / "ai" / "lib")
if LIB_DIR not in sys.path:
    sys.path.insert(0, LIB_DIR)

import fix.dirty_backup
from fix_containment_support import _repo


def _save(wt, artifacts, dirty, **kwargs):
    return fix.dirty_backup.save(wt, artifacts, dirty, **kwargs)


class TestSave:
    def test_dirty_tracked_file_backed_up_byte_exact(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        (wt / "a.py").write_text("dirty\n")
        artifacts = tmp_path / "art"

        result = _save(wt, artifacts, {"a.py"})

        dest = artifacts / "dirty-backup" / "a.py"
        assert dest.is_file()
        assert dest.read_text() == "dirty\n"
        assert result.paths == ("a.py",)
        assert result.dest == artifacts / "dirty-backup"

    def test_untracked_file_backed_up(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        (wt / "new.py").write_text("untracked\n")
        artifacts = tmp_path / "art"

        result = _save(wt, artifacts, {"new.py"})

        dest = artifacts / "dirty-backup" / "new.py"
        assert dest.is_file()
        assert dest.read_text() == "untracked\n"
        assert result.paths == ("new.py",)

    def test_dirty_symlink_to_outside_file_backed_up_as_symlink(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        outside = tmp_path / "outside.txt"
        outside.write_text("outside-secret\n")
        (wt / "link").symlink_to(outside)
        artifacts = tmp_path / "art"

        result = _save(wt, artifacts, {"link"})

        dest = artifacts / "dirty-backup" / "link"
        assert dest.is_symlink()
        assert dest.readlink() == outside
        assert result.paths == ("link",)
        copied = [
            p for p in (artifacts / "dirty-backup").rglob("*")
            if p.is_file() and not p.is_symlink()
        ]
        assert copied == []

    def test_binary_file_backed_up(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        blob = bytes(range(256)) + b"\x00\xff"
        (wt / "img.bin").write_bytes(blob)
        artifacts = tmp_path / "art"

        _save(wt, artifacts, {"img.bin"})

        dest = artifacts / "dirty-backup" / "img.bin"
        assert dest.is_file()
        assert dest.read_bytes() == blob

    def test_clean_file_not_backed_up(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n", "b.py": "clean\n"})
        (wt / "a.py").write_text("dirty\n")
        artifacts = tmp_path / "art"

        _save(wt, artifacts, {"a.py"})

        assert (artifacts / "dirty-backup" / "a.py").is_file()
        assert not (artifacts / "dirty-backup" / "b.py").exists()

    def test_oversized_file_skipped_and_recorded(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        (wt / "big.bin").write_bytes(b"x" * 64)
        artifacts = tmp_path / "art"

        with patch.object(fix.dirty_backup, "MAX_FILE_BYTES", 32):
            result = _save(wt, artifacts, {"big.bin"})

        assert result.paths == ()
        assert result.skipped == (
            fix.dirty_backup.Skipped("big.bin", "file exceeds 32 bytes"),
        )
        assert not (artifacts / "dirty-backup" / "big.bin").exists()
        recorded = json.loads(
            (artifacts / "dirty-backup.skipped.json").read_text(),
        )
        assert recorded == [{"path": "big.bin", "reason": "file exceeds 32 bytes"}]

    def test_total_cap_skips_files_past_the_budget(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        (wt / "one.bin").write_bytes(b"x" * 30)
        (wt / "two.bin").write_bytes(b"y" * 30)
        artifacts = tmp_path / "art"

        with patch.object(fix.dirty_backup, "MAX_TOTAL_BYTES", 40):
            result = _save(wt, artifacts, {"one.bin", "two.bin"})

        assert result.paths == ("one.bin",)
        assert [s.path for s in result.skipped] == ["two.bin"]
        assert not (artifacts / "dirty-backup" / "two.bin").exists()

    def test_backup_failure_does_not_raise(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        (wt / "a.py").write_text("dirty\n")
        artifacts = tmp_path / "art"
        trail = Mock()

        with patch.object(
            fix.dirty_backup, "save_clobber", side_effect=RuntimeError("disk"),
        ):
            try:
                result = _save(wt, artifacts, {"a.py"}, trail=trail)
            except Exception as exc:
                raise AssertionError(
                    f"save raised {type(exc).__name__}"
                ) from exc

        assert result.paths == ()
        assert result.dest is None
        trail.warn.assert_called_once()
        assert trail.warn.call_args.args[0] == "fix_dirty_backup_failed"

    def test_artifacts_dir_is_skipped(self, tmp_path):
        wt = _repo(tmp_path, {"a.py": "orig\n"})
        artifacts = wt / "artifacts"
        artifacts.mkdir()
        (artifacts / "tracking.md").write_text("x\n")
        (wt / "a.py").write_text("dirty\n")

        result = _save(
            wt, artifacts, {"a.py", "artifacts/tracking.md"},
            exclude="artifacts",
        )

        assert result.paths == ("a.py",)
        assert not (artifacts / "dirty-backup" / "artifacts" / "tracking.md").exists()
