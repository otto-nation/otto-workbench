from nesting.bash import BashChecker
from nesting.types import Violation


def _check(code: str, max_depth: int = 2) -> list[Violation]:
    return BashChecker().check_nesting(code.splitlines(keepends=True), max_depth)


def test_flat_script_no_violations():
    code = '''\
#!/usr/bin/env bash
echo "hello"
echo "world"
'''
    assert _check(code) == []


def test_single_if_depth_1_passes():
    code = '''\
#!/usr/bin/env bash
my_func() {
  if true; then
    echo "ok"
  fi
}
'''
    assert _check(code) == []


def test_nested_if_depth_2_passes():
    code = '''\
#!/usr/bin/env bash
my_func() {
  if true; then
    if true; then
      echo "ok"
    fi
  fi
}
'''
    assert _check(code) == []


def test_triple_nesting_violates():
    code = '''\
#!/usr/bin/env bash
my_func() {
  if true; then
    if true; then
      if true; then
        echo "too deep"
      fi
    fi
  fi
}
'''
    assert len(_check(code)) > 0


def test_function_boundary_resets():
    code = '''\
#!/usr/bin/env bash
func_a() {
  if true; then
    if true; then
      echo "depth 2"
    fi
  fi
}

func_b() {
  if true; then
    echo "depth 1 again"
  fi
}
'''
    assert _check(code) == []


def test_for_counts_as_nesting():
    code = '''\
#!/usr/bin/env bash
my_func() {
  for x in 1 2 3; do
    for y in a b c; do
      for z in i j k; do
        echo "$x $y $z"
      done
    done
  done
}
'''
    assert len(_check(code)) > 0


def test_while_counts_as_nesting():
    code = '''\
#!/usr/bin/env bash
my_func() {
  while true; do
    while true; do
      while true; do
        break
      done
    done
  done
}
'''
    assert len(_check(code)) > 0


def test_case_counts_as_nesting():
    code = '''\
#!/usr/bin/env bash
my_func() {
  if true; then
    case "$1" in
      a)
        if true; then
          echo "deep"
        fi
        ;;
    esac
  fi
}
'''
    assert len(_check(code)) > 0


def test_self_contained_oneliner_cancels_out():
    code = '''\
#!/usr/bin/env bash
my_func() {
  if true; then
    for x in 1 2 3; do echo "$x"; done
    for y in a b; do echo "$y"; done
    echo "still depth 1"
  fi
}
'''
    assert _check(code) == []


def test_heredoc_content_skipped():
    code = '''\
#!/usr/bin/env bash
my_func() {
  if true; then
    cat <<EOF
if true; then
  if true; then
    if true; then
      deeply nested in heredoc
    fi
  fi
fi
EOF
  fi
}
'''
    assert _check(code) == []


def test_single_line_function_skipped():
    code = '''\
#!/usr/bin/env bash
short() { echo "hi"; }
'''
    assert _check(code) == []


def test_bats_test_blocks_reset_depth():
    code = '''\
#!/usr/bin/env bats
@test "first test" {
  if true; then
    if true; then
      echo "depth 2"
    fi
  fi
}

@test "second test" {
  if true; then
    echo "depth 1"
  fi
}
'''
    assert _check(code) == []


def test_violation_reports_function_name():
    code = '''\
#!/usr/bin/env bash
deep_func() {
  if true; then
    if true; then
      if true; then
        echo "too deep"
      fi
    fi
  fi
}
'''
    violations = _check(code)
    assert violations[0].function_name == "deep_func"
