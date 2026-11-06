"""Set scalar keys in one top-level TOML table, preserving every other byte; refuses rather than guesses.

Usage: python3 lib/toml_table.py set FILE TABLE --out PATH KEY=JSON_SCALAR...

Reads FILE (missing = empty document), edits TABLE in place, and writes the
candidate only to --out. FILE is never touched. Desired values are JSON
scalars (str, bool, or int). TABLE is a bare key.

Exit codes: 0 candidate written, 3 already set (nothing written), 2 refused
(reason on stderr; file left as is), 1 usage or I/O error.
"""

from __future__ import annotations

import copy
import json
import re
import stat
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path

_USAGE = (
    "usage: python3 lib/toml_table.py set FILE TABLE --out PATH KEY=JSON_SCALAR..."
)
_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ANY_HEADER = re.compile(r"^\s*\[")
_KV_PREFIX = re.compile(r"^\s*[A-Za-z0-9_-]+\s*=")


@dataclass(frozen=True)
class Request:
    """One `set` invocation: table, destination, and ordered key/value pairs."""

    file: Path
    table: str
    out: Path
    keys: tuple[tuple[str, object], ...]


def _die_usage() -> None:
    print(_USAGE, file=sys.stderr)


def _parse_scalar(raw: str) -> object | None:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if type(value) is bool:
        return value
    if type(value) is int:
        return value
    if type(value) is str:
        return value
    return None


def _parse_assignment(item: str) -> tuple[str, object] | None:
    name, sep, raw = item.partition("=")
    if not sep or not _BARE_KEY.fullmatch(name):
        return None
    value = _parse_scalar(raw)
    if value is None:
        return None
    return name, value


def _parse_args(args: list[str]) -> Request | None:
    if len(args) < 6 or args[0] != "set":
        _die_usage()
        return None
    file = Path(args[1])
    table = args[2]
    if not _BARE_KEY.fullmatch(table):
        _die_usage()
        return None
    if args[3] != "--out":
        _die_usage()
        return None
    out = Path(args[4])
    pairs: list[tuple[str, object]] = []
    for item in args[5:]:
        parsed = _parse_assignment(item)
        if parsed is None:
            _die_usage()
            return None
        pairs.append(parsed)
    return Request(file=file, table=table, out=out, keys=tuple(pairs))


def _render_value(value: object) -> str:
    if type(value) is bool:
        return "true" if value else "false"
    if type(value) is int:
        return str(value)
    if type(value) is str:
        return json.dumps(value)
    raise TypeError(f"unsupported value type: {type(value)}")


def _newline(text: str) -> str:
    if "\r\n" in text:
        return "\r\n"
    return "\n"


def _ending(line: str) -> str:
    if line.endswith("\r\n"):
        return "\r\n"
    if line.endswith("\n"):
        return "\n"
    return ""


def _body(line: str) -> str:
    return line.rstrip("\r\n")


def _is_blank(line: str) -> bool:
    return _body(line).strip() == ""


def _lines(text: str) -> list[str]:
    if not text:
        return []
    raw = text.split("\n")
    out: list[str] = []
    last = len(raw) - 1
    for i, part in enumerate(raw):
        if i < last:
            out.append(part + "\n")
            continue
        if part:
            out.append(part)
    return out


def _header_re(table: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*\[\s*{re.escape(table)}\s*\]\s*(#.*)?$")


def _subtable_re(table: str) -> re.Pattern[str]:
    return re.compile(rf"^\s*\[\s*{re.escape(table)}\.")


def _kv_re(name: str) -> re.Pattern[str]:
    value = (
        r'(?:"(?:[^"\\]|\\.)*"|'
        r"'[^']*'|"
        r"true|false|"
        r"-?[0-9]+)"
    )
    return re.compile(
        rf"^(\s*{re.escape(name)}\s*=\s*)({value})(\s*(#.*)?)?$"
    )


def _header_indexes(lines: list[str], table: str) -> list[int]:
    header = _header_re(table)
    found: list[int] = []
    for i, line in enumerate(lines):
        if header.match(_body(line)):
            found.append(i)
    return found


def _section_end(lines: list[str], header_idx: int) -> int:
    start = header_idx + 1
    for i in range(start, len(lines)):
        if _ANY_HEADER.match(_body(lines[i])):
            return i
    return len(lines)


def _find_key(lines: list[str], start: int, end: int, name: str) -> int | None:
    pat = _kv_re(name)
    for i in range(start, end):
        if pat.match(_body(lines[i])):
            return i
    return None


def _last_kv_index(lines: list[str], start: int, end: int) -> int:
    last = start - 1
    for i in range(start, end):
        if _KV_PREFIX.match(_body(lines[i])):
            last = i
    return last


def _replace_value(line: str, name: str, value: object) -> str:
    match = _kv_re(name).match(_body(line))
    if match is None:
        raise ValueError(name)
    return f"{match.group(1)}{_render_value(value)}{match.group(3) or ''}{_ending(line)}"


def _render_block(table: str, keys: tuple[tuple[str, object], ...], nl: str) -> str:
    rows = [f"[{table}]"]
    for name, value in keys:
        rows.append(f"{name} = {_render_value(value)}")
    return nl.join(rows) + nl


def _append_block(text: str, block: str, nl: str) -> str:
    if not text:
        return block
    body = text
    if not body.endswith(nl):
        body += nl
    if not body.endswith(nl + nl):
        body += nl
    return body + block


def _insert_block_at(lines: list[str], idx: int, block: str, nl: str) -> str:
    prefix = lines[:idx]
    suffix = lines[idx:]
    pieces: list[str] = []
    if prefix:
        pieces.append("".join(prefix))
        if not _is_blank(prefix[-1]):
            pieces.append(nl)
    pieces.append(block)
    if suffix:
        if not _is_blank(suffix[0]):
            pieces.append(nl)
        pieces.append("".join(suffix))
    return "".join(pieces)


def _first_subtable(lines: list[str], table: str) -> int | None:
    pat = _subtable_re(table)
    for i, line in enumerate(lines):
        if pat.match(_body(line)):
            return i
    return None


def _insert_table(
    text: str, lines: list[str], table: str, keys: tuple[tuple[str, object], ...],
) -> str:
    nl = _newline(text)
    block = _render_block(table, keys, nl)
    idx = _first_subtable(lines, table)
    if idx is None:
        return _append_block(text, block, nl)
    return _insert_block_at(lines, idx, block, nl)


def _missing_keys(
    lines: list[str], start: int, end: int, keys: tuple[tuple[str, object], ...],
) -> list[tuple[str, object]]:
    missing: list[tuple[str, object]] = []
    for name, value in keys:
        if _find_key(lines, start, end, name) is None:
            missing.append((name, value))
    return missing


def _patch_section(
    lines: list[str], header_idx: int,
    keys: tuple[tuple[str, object], ...],
) -> str:
    end = _section_end(lines, header_idx)
    start = header_idx + 1
    patched = list(lines)
    for name, value in keys:
        idx = _find_key(patched, start, end, name)
        if idx is None:
            continue
        patched[idx] = _replace_value(patched[idx], name, value)
    missing = _missing_keys(patched, start, end, keys)
    if not missing:
        return "".join(patched)
    nl = _ending(patched[header_idx]) or "\n"
    insert_at = _last_kv_index(patched, start, end) + 1
    rows = [f"{name} = {_render_value(value)}{nl}" for name, value in missing]
    patched[insert_at:insert_at] = rows
    return "".join(patched)


def _edit(text: str, table: str, keys: tuple[tuple[str, object], ...]) -> str | None:
    lines = _lines(text)
    indexes = _header_indexes(lines, table)
    if len(indexes) > 1:
        return None
    if not indexes:
        return _insert_table(text, lines, table, keys)
    return _patch_section(lines, indexes[0], keys)


def _docs_equal(left: object, right: object) -> bool:
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return _dicts_equal(left, right)
    if isinstance(left, list):
        return _lists_equal(left, right)
    return left == right


def _dicts_equal(left: dict, right: dict) -> bool:
    if left.keys() != right.keys():
        return False
    for key in left:
        if not _docs_equal(left[key], right[key]):
            return False
    return True


def _lists_equal(left: list, right: list) -> bool:
    if len(left) != len(right):
        return False
    for i, item in enumerate(left):
        if not _docs_equal(item, right[i]):
            return False
    return True


def _already_set(doc: dict, table: str, keys: tuple[tuple[str, object], ...]) -> bool:
    current = doc.get(table)
    if type(current) is not dict:
        return False
    for name, value in keys:
        if name not in current:
            return False
        if type(current[name]) is not type(value) or current[name] != value:
            return False
    return True


def _expected_doc(doc: dict, table: str, keys: tuple[tuple[str, object], ...]) -> dict:
    out = copy.deepcopy(doc)
    current = out.get(table)
    if type(current) is not dict:
        current = {}
        out[table] = current
    for name, value in keys:
        current[name] = value
    return out


def _read_source(path: Path) -> tuple[str | None, int]:
    if not path.exists():
        return "", 0
    try:
        with path.open(encoding="utf-8", newline="") as handle:
            return handle.read(), 0
    except OSError as exc:
        print(f"could not read {path}: {exc}", file=sys.stderr)
        return None, 1


def _write_out(req: Request, candidate: str) -> int:
    try:
        req.out.write_bytes(candidate.encode("utf-8"))
        mode = 0o644
        if req.file.is_file():
            mode = stat.S_IMODE(req.file.stat().st_mode)
        req.out.chmod(mode)
    except OSError as exc:
        print(f"could not write {req.out}: {exc}", file=sys.stderr)
        return 1
    return 0


def _set_table(req: Request) -> int:
    text, code = _read_source(req.file)
    if text is None:
        return code
    try:
        doc = tomllib.loads(text)
    except tomllib.TOMLDecodeError:
        print("not valid TOML — left as is", file=sys.stderr)
        return 2
    if _already_set(doc, req.table, req.keys):
        return 3
    candidate = _edit(text, req.table, req.keys)
    if candidate is None:
        print(f"cannot edit [{req.table}] in place — left as is", file=sys.stderr)
        return 2
    try:
        got = tomllib.loads(candidate)
    except tomllib.TOMLDecodeError:
        print(f"cannot edit [{req.table}] in place — left as is", file=sys.stderr)
        return 2
    expected = _expected_doc(doc, req.table, req.keys)
    if not _docs_equal(got, expected):
        print(f"cannot edit [{req.table}] in place — left as is", file=sys.stderr)
        return 2
    return _write_out(req, candidate)


def main(argv: list[str] | None = None) -> int:
    req = _parse_args(sys.argv[1:] if argv is None else argv)
    if req is None:
        return 1
    return _set_table(req)


if __name__ == "__main__":
    sys.exit(main())
