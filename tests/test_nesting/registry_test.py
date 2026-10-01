from nesting import get_checker_for_extension, get_checker_for_shebang, get_all_extensions
from nesting.bash import BashChecker
from nesting.python import PythonChecker
from nesting.go import GoChecker


def test_extension_lookup_sh():
    checker = get_checker_for_extension('.sh')
    assert isinstance(checker, BashChecker)


def test_extension_lookup_bash():
    checker = get_checker_for_extension('.bash')
    assert isinstance(checker, BashChecker)


def test_extension_lookup_bats():
    checker = get_checker_for_extension('.bats')
    assert isinstance(checker, BashChecker)


def test_extension_lookup_py():
    checker = get_checker_for_extension('.py')
    assert isinstance(checker, PythonChecker)


def test_extension_lookup_go():
    checker = get_checker_for_extension('.go')
    assert isinstance(checker, GoChecker)


def test_extension_lookup_unknown_returns_none():
    assert get_checker_for_extension('.rs') is None


def test_shebang_lookup_bash():
    checker = get_checker_for_shebang(b'#!/usr/bin/env bash\n')
    assert isinstance(checker, BashChecker)


def test_shebang_lookup_python():
    checker = get_checker_for_shebang(b'#!/usr/bin/env python3\n')
    assert isinstance(checker, PythonChecker)


def test_shebang_lookup_bash_direct_path():
    checker = get_checker_for_shebang(b'#!/bin/bash\n')
    assert isinstance(checker, BashChecker)


def test_shebang_lookup_sh_direct_path():
    checker = get_checker_for_shebang(b'#!/bin/sh\n')
    assert isinstance(checker, BashChecker)


def test_shebang_lookup_python_direct_path():
    checker = get_checker_for_shebang(b'#!/usr/bin/python\n')
    assert isinstance(checker, PythonChecker)


def test_shebang_lookup_unknown_returns_none():
    assert get_checker_for_shebang(b'#!/usr/bin/env ruby\n') is None


def test_get_all_extensions():
    exts = get_all_extensions()
    assert '.sh' in exts
    assert '.py' in exts
    assert '.go' in exts
    assert '.bash' in exts
    assert '.bats' in exts
