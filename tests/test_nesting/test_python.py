from nesting.python import PythonChecker
from nesting.types import Violation


def _check(code: str, max_depth: int = 2) -> list[Violation]:
    return PythonChecker().check_nesting(code.splitlines(keepends=True), max_depth)


def test_flat_script_no_violations():
    code = '''\
#!/usr/bin/env python3
print("hello")
x = 1 + 2
'''
    assert _check(code) == []


def test_single_if_depth_1_passes():
    code = '''\
def foo():
    if True:
        print("ok")
'''
    assert _check(code) == []


def test_nested_if_for_depth_2_passes():
    code = '''\
def foo():
    if True:
        for x in range(10):
            print(x)
'''
    assert _check(code) == []


def test_triple_nesting_violates():
    code = '''\
def foo():
    if True:
        for x in range(10):
            if x > 5:
                print("too deep")
'''
    assert len(_check(code)) > 0


def test_def_resets_depth():
    code = '''\
def func_a():
    if True:
        for x in range(10):
            print(x)

def func_b():
    if True:
        print("depth 1 again")
'''
    assert _check(code) == []


def test_class_resets_depth():
    code = '''\
def deep():
    if True:
        for x in range(10):
            print(x)

class Foo:
    def bar(self):
        if True:
            print("depth 1")
'''
    assert _check(code) == []


def test_elif_else_do_not_increase_depth():
    code = '''\
def foo():
    if True:
        for x in range(10):
            print(x)
    elif False:
        for y in range(5):
            print(y)
    else:
        for z in range(3):
            print(z)
'''
    assert _check(code) == []


def test_try_except_finally_do_not_increase_depth():
    code = '''\
def foo():
    try:
        for x in range(10):
            print(x)
    except ValueError:
        for y in range(5):
            print(y)
    finally:
        for z in range(3):
            print(z)
'''
    assert _check(code) == []


def test_with_counts_as_nesting():
    code = '''\
def foo():
    with open("f") as fh:
        for line in fh:
            if line:
                print("too deep")
'''
    assert len(_check(code)) > 0


def test_triple_quoted_string_skipped():
    code = '''\
def foo():
    if True:
        x = """
        if True:
            if True:
                if True:
                    deeply nested in string
        """
        print(x)
'''
    assert _check(code) == []


def test_violation_reports_function_name():
    code = '''\
def deep_func():
    if True:
        for x in range(10):
            if x > 5:
                print("too deep")
'''
    violations = _check(code)
    assert len(violations) > 0
    assert violations[0].function_name == "deep_func"


def test_nested_def_resets_independently():
    code = '''\
def outer():
    if True:
        for x in range(10):
            print(x)

    def inner():
        if True:
            print("depth 1 in inner")
'''
    assert _check(code) == []


def test_decorator_lines_skipped():
    code = '''\
class Foo:
    @staticmethod
    def bar():
        if True:
            for x in range(10):
                print(x)
'''
    assert _check(code) == []


def test_deep_nesting_inside_elif_violates():
    code = '''\
def foo():
    if True:
        print("shallow")
    elif False:
        for x in range(10):
            if x > 5:
                print("too deep inside elif")
'''
    assert len(_check(code)) > 0


def test_deep_nesting_inside_else_violates():
    code = '''\
def foo():
    if True:
        print("shallow")
    else:
        for x in range(10):
            if x > 5:
                print("too deep inside else")
'''
    assert len(_check(code)) > 0


def test_deep_nesting_inside_except_violates():
    code = '''\
def foo():
    try:
        print("shallow")
    except ValueError:
        for x in range(10):
            if x > 5:
                print("too deep inside except")
'''
    assert len(_check(code)) > 0


def test_async_for_with_count_as_nesting():
    code = '''\
async def foo():
    async with aopen("f") as fh:
        async for line in fh:
            if line:
                print("too deep")
'''
    assert len(_check(code)) > 0
