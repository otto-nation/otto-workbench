#!/usr/bin/env bats
# Tests for validate-nesting Python script — string stripping, nesting depth
# analysis, script discovery, and CLI behavior.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATE_NESTING="$REPO_ROOT/bin/validate-nesting"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

# Helper: run Python expression importing from validate-nesting
_py() {
  python3 -c "
import sys, importlib.util, importlib.machinery
loader = importlib.machinery.SourceFileLoader('vn', '$VALIDATE_NESTING')
spec = importlib.util.spec_from_loader('vn', loader)
mod = importlib.util.module_from_spec(spec)
sys.modules['vn'] = mod
spec.loader.exec_module(mod)
$1
"
}

# Helper: run Python expression importing from nesting.preprocess
_preprocess() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/lib')
from nesting.preprocess import strip_strings_and_comments
$1
"
}

# Helper: write a bash script to a temp file and return its path
_write_script() {
  local name="$1"
  local path="$TMPDIR/$name"
  cat > "$path"
  echo "$path"
}

# ── strip_strings_and_comments ───────────────────────────────────────────────

@test "strip_strings_and_comments: removes single-quoted strings" {
  result=$(_preprocess "print(strip_strings_and_comments(\"echo 'hello world'\"))")
  [ "$result" = "echo " ]
}

@test "strip_strings_and_comments: removes double-quoted strings" {
  result=$(_preprocess 'print(strip_strings_and_comments("echo \"hello\""))')
  [ "$result" = "echo " ]
}

@test "strip_strings_and_comments: removes comments" {
  result=$(_preprocess "print(strip_strings_and_comments('code # comment'))")
  [ "$result" = "code " ]
}

@test "strip_strings_and_comments: keywords inside strings are stripped" {
  result=$(_preprocess "print(strip_strings_and_comments(\"echo 'if then fi'\"))")
  [[ "$result" != *"if"* ]]
  [[ "$result" != *"fi"* ]]
}

@test "strip_strings_and_comments: line with no strings or comments unchanged" {
  result=$(_preprocess "print(strip_strings_and_comments('for x in y; do echo; done'))")
  [ "$result" = "for x in y; do echo; done" ]
}

# ── check_file_bash ──────────────────────────────────────────────────────────

@test "check_file_bash: flat script has no violations" {
  local f
  f=$(_write_script "flat.sh" <<'SCRIPT'
#!/usr/bin/env bash
echo "hello"
echo "world"
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_bash: single if/fi at depth 1 passes with max_depth 2" {
  local f
  f=$(_write_script "single_if.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  if true; then
    echo "ok"
  fi
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_bash: nested if/if at depth 2 passes with max_depth 2" {
  local f
  f=$(_write_script "double_if.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  if true; then
    if true; then
      echo "ok"
    fi
  fi
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_bash: triple nesting violates max_depth 2" {
  local f
  f=$(_write_script "triple_if.sh" <<'SCRIPT'
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
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_bash: function boundary resets depth" {
  local f
  f=$(_write_script "two_funcs.sh" <<'SCRIPT'
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
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_bash: for/do/done counts as nesting" {
  local f
  f=$(_write_script "for_loop.sh" <<'SCRIPT'
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
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_bash: while/do/done counts as nesting" {
  local f
  f=$(_write_script "while_loop.sh" <<'SCRIPT'
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
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_bash: case/esac counts as nesting" {
  local f
  f=$(_write_script "case.sh" <<'SCRIPT'
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
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_bash: self-contained one-liner cancels out" {
  local f
  f=$(_write_script "oneliner.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  if true; then
    for x in 1 2 3; do echo "$x"; done
    for y in a b; do echo "$y"; done
    echo "still depth 1"
  fi
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_bash: heredoc content is skipped" {
  local f
  f=$(_write_script "heredoc.sh" <<'SCRIPT'
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
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_bash: single-line function is skipped" {
  local f
  f=$(_write_script "oneline_func.sh" <<'SCRIPT'
#!/usr/bin/env bash
short() { echo "hi"; }
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_bash: @test blocks reset depth in bats files" {
  local f
  f="$BATS_TEST_TMPDIR/tests.bats"
  printf '%s\n' '#!/usr/bin/env bats' \
    '@test "first test" {' \
    '  if true; then' \
    '    if true; then' \
    '      echo "depth 2"' \
    '    fi' \
    '  fi' \
    '}' \
    '' \
    '@test "second test" {' \
    '  if true; then' \
    '    echo "depth 1 — reset by @test boundary"' \
    '  fi' \
    '}' > "$f"
  result=$(_py "print(len(mod.check_file_bash('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_bash: violation reports function name" {
  local f
  f=$(_write_script "named.sh" <<'SCRIPT'
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
SCRIPT
  )
  result=$(_py "v = mod.check_file_bash('$f', 2); print(v[0][2])")
  [ "$result" = "deep_func" ]
}

# ── discover_scripts ─────────────────────────────────────────────────────────

@test "discover_scripts: finds .sh files with bash shebang" {
  mkdir -p "$TMPDIR/repo"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMPDIR/repo/test.sh"
  result=$(_py "scripts = mod.discover_scripts('$TMPDIR/repo'); print(len(scripts), scripts[0][1])")
  [ "$result" = "1 bash" ]
}

@test "discover_scripts: skips .sh files without bash shebang" {
  mkdir -p "$TMPDIR/repo"
  printf '#!/usr/bin/env python3\nprint("hi")\n' > "$TMPDIR/repo/test.sh"
  result=$(_py "print(len(mod.discover_scripts('$TMPDIR/repo')))")
  [ "$result" = "0" ]
}

@test "discover_scripts: finds extensionless bash scripts in bin/" {
  mkdir -p "$TMPDIR/repo/bin"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMPDIR/repo/bin/mytool"
  result=$(_py "scripts = mod.discover_scripts('$TMPDIR/repo'); print(len(scripts), scripts[0][1])")
  [ "$result" = "1 bash" ]
}

@test "discover_scripts: excludes .git directory" {
  mkdir -p "$TMPDIR/repo/.git/hooks"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMPDIR/repo/.git/hooks/pre-commit.sh"
  result=$(_py "print(len(mod.discover_scripts('$TMPDIR/repo')))")
  [ "$result" = "0" ]
}

@test "discover_scripts: finds .bats files as bash" {
  mkdir -p "$TMPDIR/repo"
  printf '#!/usr/bin/env bats\n@test "x" { true; }\n' > "$TMPDIR/repo/test.bats"
  result=$(_py "scripts = mod.discover_scripts('$TMPDIR/repo'); print(len(scripts), scripts[0][1])")
  [ "$result" = "1 bash" ]
}

@test "discover_scripts: finds .py files with python shebang" {
  mkdir -p "$TMPDIR/repo"
  printf '#!/usr/bin/env python3\nprint("hi")\n' > "$TMPDIR/repo/test.py"
  result=$(_py "scripts = mod.discover_scripts('$TMPDIR/repo'); print(len(scripts), scripts[0][1])")
  [ "$result" = "1 python" ]
}

@test "discover_scripts: finds extensionless python scripts in bin/" {
  mkdir -p "$TMPDIR/repo/bin"
  printf '#!/usr/bin/env python3\nprint("hi")\n' > "$TMPDIR/repo/bin/mypytool"
  result=$(_py "scripts = mod.discover_scripts('$TMPDIR/repo'); print(len(scripts), scripts[0][1])")
  [ "$result" = "1 python" ]
}

@test "discover_scripts: mixed bash and python sorted by path" {
  mkdir -p "$TMPDIR/repo"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMPDIR/repo/a_bash.sh"
  printf '#!/usr/bin/env python3\nprint("hi")\n' > "$TMPDIR/repo/b_python.py"
  result=$(_py "scripts = mod.discover_scripts('$TMPDIR/repo'); print(len(scripts), scripts[0][1], scripts[1][1])")
  [ "$result" = "2 bash python" ]
}

# ── check_file_python ────────────────────────────────────────────────────────

@test "check_file_python: flat script has no violations" {
  local f
  f=$(_write_script "flat.py" <<'SCRIPT'
#!/usr/bin/env python3
print("hello")
x = 1 + 2
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: single if at depth 1 passes max_depth 2" {
  local f
  f=$(_write_script "single_if.py" <<'SCRIPT'
#!/usr/bin/env python3
def foo():
    if True:
        print("ok")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: nested if/for at depth 2 passes max_depth 2" {
  local f
  f=$(_write_script "double.py" <<'SCRIPT'
#!/usr/bin/env python3
def foo():
    if True:
        for x in range(10):
            print(x)
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: triple nesting violates max_depth 2" {
  local f
  f=$(_write_script "triple.py" <<'SCRIPT'
#!/usr/bin/env python3
def foo():
    if True:
        for x in range(10):
            if x > 5:
                print("too deep")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_python: def resets depth" {
  local f
  f=$(_write_script "two_funcs.py" <<'SCRIPT'
#!/usr/bin/env python3
def func_a():
    if True:
        for x in range(10):
            print(x)

def func_b():
    if True:
        print("depth 1 again")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: class resets depth" {
  local f
  f=$(_write_script "cls.py" <<'SCRIPT'
#!/usr/bin/env python3
def deep():
    if True:
        for x in range(10):
            print(x)

class Foo:
    def bar(self):
        if True:
            print("depth 1")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: elif/else do not increase depth" {
  local f
  f=$(_write_script "elif.py" <<'SCRIPT'
#!/usr/bin/env python3
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
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: try/except/finally do not increase depth" {
  local f
  f=$(_write_script "tryexcept.py" <<'SCRIPT'
#!/usr/bin/env python3
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
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: with statement counts as nesting" {
  local f
  f=$(_write_script "with.py" <<'SCRIPT'
#!/usr/bin/env python3
def foo():
    with open("f") as fh:
        for line in fh:
            if line:
                print("too deep")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_python: triple-quoted string content is skipped" {
  local f
  f=$(_write_script "tripleq.py" <<'SCRIPT'
#!/usr/bin/env python3
def foo():
    if True:
        x = """
        if True:
            if True:
                if True:
                    deeply nested in string
        """
        print(x)
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: violation reports function name" {
  local f
  f=$(_write_script "named.py" <<'SCRIPT'
#!/usr/bin/env python3
def deep_func():
    if True:
        for x in range(10):
            if x > 5:
                print("too deep")
SCRIPT
  )
  result=$(_py "v = mod.check_file_python('$f', 2); print(v[0][2])")
  [ "$result" = "deep_func" ]
}

@test "check_file_python: nested def resets independently" {
  local f
  f=$(_write_script "nested_def.py" <<'SCRIPT'
#!/usr/bin/env python3
def outer():
    if True:
        for x in range(10):
            print(x)

    def inner():
        if True:
            print("depth 1 in inner")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: decorator lines are skipped" {
  local f
  f=$(_write_script "deco.py" <<'SCRIPT'
#!/usr/bin/env python3
class Foo:
    @staticmethod
    def bar():
        if True:
            for x in range(10):
                print(x)
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file_python: deep nesting inside elif violates max_depth" {
  local f
  f=$(_write_script "elif_deep.py" <<'SCRIPT'
#!/usr/bin/env python3
def foo():
    if True:
        print("shallow")
    elif False:
        for x in range(10):
            if x > 5:
                print("too deep inside elif")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_python: deep nesting inside else violates max_depth" {
  local f
  f=$(_write_script "else_deep.py" <<'SCRIPT'
#!/usr/bin/env python3
def foo():
    if True:
        print("shallow")
    else:
        for x in range(10):
            if x > 5:
                print("too deep inside else")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_python: deep nesting inside except violates max_depth" {
  local f
  f=$(_write_script "except_deep.py" <<'SCRIPT'
#!/usr/bin/env python3
def foo():
    try:
        print("shallow")
    except ValueError:
        for x in range(10):
            if x > 5:
                print("too deep inside except")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_python: triple quotes in regex pattern do not suppress violations" {
  local f
  f=$(_write_script "triple_in_regex.py" <<'PYEOF'
#!/usr/bin/env python3
import re
TRIPLE = re.compile(r'"""|\'\'\'')

def foo():
    if True:
        for x in range(10):
            if x > 5:
                print("too deep")
PYEOF
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file_python: async for/with count as nesting" {
  local f
  f=$(_write_script "asyncn.py" <<'SCRIPT'
#!/usr/bin/env python3
async def foo():
    async with aopen("f") as fh:
        async for line in fh:
            if line:
                print("too deep")
SCRIPT
  )
  result=$(_py "print(len(mod.check_file_python('$f', 2)))")
  [ "$result" -gt 0 ]
}

# ── detect_language ──────────────────────────────────────────────────────────

@test "detect_language: .py returns python" {
  printf '#!/usr/bin/env python3\n' > "$TMPDIR/test.py"
  result=$(_py "print(mod.detect_language('$TMPDIR/test.py'))")
  [ "$result" = "python" ]
}

@test "detect_language: .sh returns bash" {
  printf '#!/usr/bin/env bash\n' > "$TMPDIR/test.sh"
  result=$(_py "print(mod.detect_language('$TMPDIR/test.sh'))")
  [ "$result" = "bash" ]
}

@test "detect_language: .bats returns bash" {
  printf '#!/usr/bin/env bats\n' > "$TMPDIR/test.bats"
  result=$(_py "print(mod.detect_language('$TMPDIR/test.bats'))")
  [ "$result" = "bash" ]
}

@test "detect_language: extensionless with python shebang returns python" {
  printf '#!/usr/bin/env python3\n' > "$TMPDIR/myscript"
  result=$(_py "print(mod.detect_language('$TMPDIR/myscript'))")
  [ "$result" = "python" ]
}

@test "detect_language: extensionless with bash shebang returns bash" {
  printf '#!/usr/bin/env bash\n' > "$TMPDIR/myscript"
  result=$(_py "print(mod.detect_language('$TMPDIR/myscript'))")
  [ "$result" = "bash" ]
}

# ── CLI ──────────────────────────────────────────────────────────────────────

@test "validate-nesting --help exits 0" {
  run "$VALIDATE_NESTING" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"nesting depth"* ]]
}

@test "validate-nesting: passing file exits 0" {
  local f
  f=$(_write_script "good.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  if true; then
    echo "ok"
  fi
}
SCRIPT
  )
  run "$VALIDATE_NESTING" "$f"
  [ "$status" -eq 0 ]
}

@test "validate-nesting: failing file exits 1" {
  local f
  f=$(_write_script "bad.sh" <<'SCRIPT'
#!/usr/bin/env bash
bad_func() {
  if true; then
    if true; then
      if true; then
        echo "too deep"
      fi
    fi
  fi
}
SCRIPT
  )
  run "$VALIDATE_NESTING" "$f"
  [ "$status" -eq 1 ]
}

@test "validate-nesting: --max-depth 3 allows deeper nesting" {
  local f
  f=$(_write_script "deep_ok.sh" <<'SCRIPT'
#!/usr/bin/env bash
func() {
  if true; then
    if true; then
      if true; then
        echo "ok at depth 3"
      fi
    fi
  fi
}
SCRIPT
  )
  run "$VALIDATE_NESTING" --max-depth 3 "$f"
  [ "$status" -eq 0 ]
}

@test "validate-nesting: python file with excessive nesting exits 1" {
  local f
  f=$(_write_script "bad.py" <<'SCRIPT'
#!/usr/bin/env python3
def bad():
    if True:
        for x in range(10):
            if x > 5:
                print("too deep")
SCRIPT
  )
  run "$VALIDATE_NESTING" "$f"
  [ "$status" -eq 1 ]
}

@test "validate-nesting: python file within limit exits 0" {
  local f
  f=$(_write_script "good.py" <<'SCRIPT'
#!/usr/bin/env python3
def good():
    if True:
        for x in range(10):
            print(x)
SCRIPT
  )
  run "$VALIDATE_NESTING" "$f"
  [ "$status" -eq 0 ]
}

@test "validate-nesting: mixed bash and python files" {
  local bash_f python_f
  bash_f=$(_write_script "ok.sh" <<'SCRIPT'
#!/usr/bin/env bash
ok() {
  if true; then
    echo "ok"
  fi
}
SCRIPT
  )
  python_f=$(_write_script "ok.py" <<'SCRIPT'
#!/usr/bin/env python3
def ok():
    if True:
        print("ok")
SCRIPT
  )
  run "$VALIDATE_NESTING" "$bash_f" "$python_f"
  [ "$status" -eq 0 ]
}
