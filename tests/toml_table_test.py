"""Tests for lib/toml_table.py — in-place TOML table edits that preserve every other byte."""

from __future__ import annotations

import tomllib
from pathlib import Path

from conftest import _load_lib

toml_table = _load_lib("toml_table")

KEYS = (
    'name="gruvbox"',
    "auto_switch=true",
    'light_name="gruvbox-light"',
    'dark_name="gruvbox"',
)

THEME_BLOCK = (
    "[theme]\n"
    'name = "gruvbox"\n'
    "auto_switch = true\n"
    'light_name = "gruvbox-light"\n'
    'dark_name = "gruvbox"\n'
)


def _run(src: Path, out: Path, keys: tuple[str, ...] = KEYS, table: str = "theme") -> int:
    return toml_table.main(["set", str(src), table, "--out", str(out), *keys])


def _write(path: Path, text: str) -> Path:
    path.write_bytes(text.encode("utf-8"))
    return path


def test_preserves_everything_else(tmp_path):
    src = _write(tmp_path / "in.toml", (
        "# leading comment\n"
        "onboarding = false\n"
        "\n"
        "[keys]\n"
        'leader = " "\n'
        "\n"
        "[theme]\n"
        "# a comment line\n"
        'name = "catppuccin"  # trailing\n'
        "auto_switch = false\n"
        "\n"
        "[theme.custom]\n"
        'accent = "#ff0000"\n'
        "\n"
        "[theme.custom.light]\n"
        'accent = "#aaaaaa"\n'
        "\n"
        "[theme.custom.dark]\n"
        'accent = "#111111"\n'
    ))
    out = tmp_path / "out.toml"
    assert _run(src, out) == 0
    assert out.read_bytes() == (
        "# leading comment\n"
        "onboarding = false\n"
        "\n"
        "[keys]\n"
        'leader = " "\n'
        "\n"
        "[theme]\n"
        "# a comment line\n"
        'name = "gruvbox"  # trailing\n'
        "auto_switch = true\n"
        'light_name = "gruvbox-light"\n'
        'dark_name = "gruvbox"\n'
        "\n"
        "[theme.custom]\n"
        'accent = "#ff0000"\n'
        "\n"
        "[theme.custom.light]\n"
        'accent = "#aaaaaa"\n'
        "\n"
        "[theme.custom.dark]\n"
        'accent = "#111111"\n'
    ).encode("utf-8")


def test_updates_in_place_and_inserts_missing_after_last_key(tmp_path):
    src = _write(tmp_path / "in.toml", (
        "[theme]\n"
        "auto_switch = false\n"
        'name = "paper"\n'
    ))
    out = tmp_path / "out.toml"
    assert _run(src, out) == 0
    assert out.read_text(encoding="utf-8") == (
        "[theme]\n"
        "auto_switch = true\n"
        'name = "gruvbox"\n'
        'light_name = "gruvbox-light"\n'
        'dark_name = "gruvbox"\n'
    )


def test_inserts_block_above_subtable(tmp_path):
    src = _write(tmp_path / "in.toml", (
        "onboarding = false\n"
        "\n"
        "[theme.custom]\n"
        'accent = "#ff0000"\n'
    ))
    out = tmp_path / "out.toml"
    assert _run(src, out) == 0
    text = out.read_text(encoding="utf-8")
    assert text == (
        "onboarding = false\n"
        "\n"
        + THEME_BLOCK
        + "\n"
        "[theme.custom]\n"
        'accent = "#ff0000"\n'
    )
    doc = tomllib.loads(text)
    assert doc["theme"]["custom"] == {"accent": "#ff0000"}


def test_appends_at_eof_and_missing_file_is_the_block(tmp_path):
    src = _write(tmp_path / "in.toml", "onboarding = false\n")
    out = tmp_path / "out.toml"
    assert _run(src, out) == 0
    assert out.read_text(encoding="utf-8") == "onboarding = false\n\n" + THEME_BLOCK

    missing = tmp_path / "no-such.toml"
    out2 = tmp_path / "new.toml"
    assert _run(missing, out2) == 0
    assert out2.read_bytes() == THEME_BLOCK.encode("utf-8")

    empty = _write(tmp_path / "empty.toml", "")
    out3 = tmp_path / "from-empty.toml"
    assert _run(empty, out3) == 0
    assert out3.read_bytes() == THEME_BLOCK.encode("utf-8")


def test_already_set_writes_nothing_and_strict_types(tmp_path, capsys):
    src = _write(tmp_path / "in.toml", THEME_BLOCK)
    out = tmp_path / "out.toml"
    assert _run(src, out) == 3
    assert not out.exists()
    capsys.readouterr()

    typed = _write(tmp_path / "int.toml", (
        "[theme]\n"
        'name = "gruvbox"\n'
        "auto_switch = 1\n"
        'light_name = "gruvbox-light"\n'
        'dark_name = "gruvbox"\n'
    ))
    out2 = tmp_path / "from-int.toml"
    assert _run(typed, out2) == 0
    assert "auto_switch = true\n" in out2.read_text(encoding="utf-8")

    as_str = _write(tmp_path / "str.toml", (
        "[theme]\n"
        'name = "gruvbox"\n'
        'auto_switch = "true"\n'
        'light_name = "gruvbox-light"\n'
        'dark_name = "gruvbox"\n'
    ))
    out3 = tmp_path / "from-str.toml"
    assert _run(as_str, out3) == 0
    assert 'auto_switch = true\n' in out3.read_text(encoding="utf-8")


def test_unparseable_input_refused(tmp_path, capsys):
    src = _write(tmp_path / "bad.toml", "[theme\n")
    out = tmp_path / "out.toml"
    assert _run(src, out) == 2
    assert not out.exists()
    assert "not valid TOML" in capsys.readouterr().err


def test_refusals_leave_no_output(tmp_path, capsys):
    cases = {
        "dotted.toml": 'theme.name = "x"\n',
        "inline.toml": 'theme = { name = "x" }\n',
        "multiline.toml": '[theme]\nname = """\nmulti\nline"""\n',
        "quoted.toml": '[theme]\n"name" = "x"\n',
        "in-string.toml": 'foo = """\nhello\n[theme]\n"""\n',
    }
    for name, text in cases.items():
        src = _write(tmp_path / name, text)
        out = tmp_path / f"{name}.out"
        assert _run(src, out) == 2, name
        assert not out.exists(), name
        err = capsys.readouterr().err
        assert "cannot edit [theme] in place" in err, name


def test_crlf_and_mode(tmp_path):
    src = tmp_path / "crlf.toml"
    src.write_bytes(
        b"[theme]\r\n"
        b'name = "paper"\r\n'
        b"auto_switch = false\r\n"
    )
    src.chmod(0o600)
    out = tmp_path / "out.toml"
    assert _run(src, out) == 0
    data = out.read_bytes()
    assert b"\r\n" in data
    assert b"\n" not in data.replace(b"\r\n", b"")
    assert data.decode("utf-8").startswith("[theme]\r\n")
    assert 'name = "gruvbox"' in data.decode("utf-8")
    assert oct(out.stat().st_mode & 0o777) == "0o600"

    missing = tmp_path / "missing.toml"
    created = tmp_path / "created.toml"
    assert _run(missing, created) == 0
    assert oct(created.stat().st_mode & 0o777) == "0o644"


def test_usage_errors(tmp_path, capsys):
    src = _write(tmp_path / "in.toml", THEME_BLOCK)
    out = tmp_path / "out.toml"
    assert toml_table.main(["set", str(src), "theme.nested", "--out", str(out), *KEYS]) == 1
    assert "usage:" in capsys.readouterr().err
    assert toml_table.main(
        ["set", str(src), "theme", "--out", str(out), "name=[1]"],
    ) == 1
    assert "usage:" in capsys.readouterr().err
    assert toml_table.main(["get", str(src)]) == 1
