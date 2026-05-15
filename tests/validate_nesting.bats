#!/usr/bin/env bats
# Tests for validate-nesting Python script — string stripping, nesting depth
# analysis, script discovery, and CLI behavior.

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATE_NESTING="$REPO_ROOT/bin/local/validate-nesting"
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

# Helper: write a bash script to a temp file and return its path
_write_script() {
  local name="$1"
  local path="$TMPDIR/$name"
  cat > "$path"
  echo "$path"
}

# ── strip_strings_and_comments ───────────────────────────────────────────────

@test "strip_strings_and_comments: removes single-quoted strings" {
  result=$(_py "print(mod.strip_strings_and_comments(\"echo 'hello world'\"))")
  [ "$result" = "echo " ]
}

@test "strip_strings_and_comments: removes double-quoted strings" {
  result=$(_py 'print(mod.strip_strings_and_comments("echo \"hello\""))')
  [ "$result" = "echo " ]
}

@test "strip_strings_and_comments: removes comments" {
  result=$(_py "print(mod.strip_strings_and_comments('code # comment'))")
  [ "$result" = "code " ]
}

@test "strip_strings_and_comments: keywords inside strings are stripped" {
  result=$(_py "print(mod.strip_strings_and_comments(\"echo 'if then fi'\"))")
  [[ "$result" != *"if"* ]]
  [[ "$result" != *"fi"* ]]
}

@test "strip_strings_and_comments: line with no strings or comments unchanged" {
  result=$(_py "print(mod.strip_strings_and_comments('for x in y; do echo; done'))")
  [ "$result" = "for x in y; do echo; done" ]
}

# ── check_file ───────────────────────────────────────────────────────────────

@test "check_file: flat script has no violations" {
  local f
  f=$(_write_script "flat.sh" <<'SCRIPT'
#!/usr/bin/env bash
echo "hello"
echo "world"
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file: single if/fi at depth 1 passes with max_depth 2" {
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
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file: nested if/if at depth 2 passes with max_depth 2" {
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
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file: triple nesting violates max_depth 2" {
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
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file: function boundary resets depth" {
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
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file: for/do/done counts as nesting" {
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
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file: while/do/done counts as nesting" {
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
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file: case/esac counts as nesting" {
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
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" -gt 0 ]
}

@test "check_file: self-contained one-liner cancels out" {
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
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file: heredoc content is skipped" {
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
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file: single-line function is skipped" {
  local f
  f=$(_write_script "oneline_func.sh" <<'SCRIPT'
#!/usr/bin/env bash
short() { echo "hi"; }
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f', 2)))")
  [ "$result" = "0" ]
}

@test "check_file: violation reports function name" {
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
  result=$(_py "
v = mod.check_file('$f', 2)
print(v[0][2])
")
  [ "$result" = "deep_func" ]
}

# ── discover_scripts ─────────────────────────────────────────────────────────

@test "discover_scripts: finds .sh files with bash shebang" {
  mkdir -p "$TMPDIR/repo"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMPDIR/repo/test.sh"
  result=$(_py "print(len(mod.discover_scripts('$TMPDIR/repo')))")
  [ "$result" = "1" ]
}

@test "discover_scripts: skips files without bash shebang" {
  mkdir -p "$TMPDIR/repo"
  printf '#!/usr/bin/env python3\nprint("hi")\n' > "$TMPDIR/repo/test.sh"
  result=$(_py "print(len(mod.discover_scripts('$TMPDIR/repo')))")
  [ "$result" = "0" ]
}

@test "discover_scripts: finds extensionless scripts in bin/" {
  mkdir -p "$TMPDIR/repo/bin"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMPDIR/repo/bin/mytool"
  result=$(_py "print(len(mod.discover_scripts('$TMPDIR/repo')))")
  [ "$result" = "1" ]
}

@test "discover_scripts: excludes .git directory" {
  mkdir -p "$TMPDIR/repo/.git/hooks"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMPDIR/repo/.git/hooks/pre-commit.sh"
  result=$(_py "print(len(mod.discover_scripts('$TMPDIR/repo')))")
  [ "$result" = "0" ]
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
