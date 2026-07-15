import re

from nesting.bash import BashChecker
from nesting.go import GoChecker
from nesting.python import PYTHON_SHEBANG_RE, PythonChecker

_CHECKERS = [BashChecker(), PythonChecker(), GoChecker()]

_EXT_MAP: dict[str, object] = {}
for _checker in _CHECKERS:
    for _ext in _checker.EXTENSIONS:
        _EXT_MAP[_ext] = _checker

_SHEBANG_PATTERNS: list[tuple[re.Pattern[bytes], object]] = [
    (_checker.SHEBANG_RE, _checker)
    for _checker in _CHECKERS
    if _checker.SHEBANG_RE is not None
]
_SHEBANG_PATTERNS.append((PYTHON_SHEBANG_RE, PythonChecker()))


def get_checker_for_extension(ext: str):
    return _EXT_MAP.get(ext)


def get_checker_for_shebang(shebang: bytes):
    for pattern, checker in _SHEBANG_PATTERNS:
        if pattern.search(shebang):
            return checker
    return None


def get_all_extensions() -> set[str]:
    return set(_EXT_MAP.keys())
