#!/usr/bin/env bats
# Integration tests for validate-nesting CLI and nesting registry.
# Checker-logic unit tests live in tests/test_nesting/ (pytest).

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

# Helper: import from nesting package
_py() {
  python3 -c "
import sys
sys.path.insert(0, '$REPO_ROOT/lib')
$1
"
}

# Helper: write a script to a temp file and return its path
_write_script() {
  local name="$1"
  local path="$TMPDIR/$name"
  cat > "$path"
  echo "$path"
}

# ── Registry: extension lookup ──────────────────────────────────────────────

@test "registry: .sh returns BashChecker" {
  result=$(_py "from nesting import get_checker_for_extension; print(type(get_checker_for_extension('.sh')).__name__)")
  [ "$result" = "BashChecker" ]
}

@test "registry: .py returns PythonChecker" {
  result=$(_py "from nesting import get_checker_for_extension; print(type(get_checker_for_extension('.py')).__name__)")
  [ "$result" = "PythonChecker" ]
}

@test "registry: .go returns GoChecker" {
  result=$(_py "from nesting import get_checker_for_extension; print(type(get_checker_for_extension('.go')).__name__)")
  [ "$result" = "GoChecker" ]
}

@test "registry: .bats returns BashChecker" {
  result=$(_py "from nesting import get_checker_for_extension; print(type(get_checker_for_extension('.bats')).__name__)")
  [ "$result" = "BashChecker" ]
}

@test "registry: unknown extension returns None" {
  result=$(_py "from nesting import get_checker_for_extension; print(get_checker_for_extension('.rs'))")
  [ "$result" = "None" ]
}

@test "registry: python shebang returns PythonChecker" {
  result=$(_py "from nesting import get_checker_for_shebang; print(type(get_checker_for_shebang(b'#!/usr/bin/env python3\n')).__name__)")
  [ "$result" = "PythonChecker" ]
}

# ── CLI: basic behavior ────────────────────────────────────────────────────

@test "validate-nesting --help exits 0" {
  run "$VALIDATE_NESTING" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"nesting depth"* ]]
}

@test "validate-nesting: passing bash file exits 0" {
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

@test "validate-nesting: failing bash file exits 1" {
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

# ── CLI: extensionless scripts (shebang detection) ────────────────────────

@test "validate-nesting: extensionless python script detected via shebang" {
  local f
  f=$(_write_script "my-script" <<'SCRIPT'
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

@test "validate-nesting: extensionless python script within limit exits 0" {
  local f
  f=$(_write_script "my-script" <<'SCRIPT'
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

# ── CLI: Go support ────────────────────────────────────────────────────────

@test "validate-nesting: go file within default depth 2 exits 0" {
  local f
  f=$(_write_script "good.go" <<'SCRIPT'
package main

func process() {
    for _, item := range items {
        if item.Valid {
            fmt.Println("depth 2 — within Go default")
        }
    }
}
SCRIPT
  )
  run "$VALIDATE_NESTING" "$f"
  [ "$status" -eq 0 ]
}

@test "validate-nesting: go file depth 3 violates default" {
  local f
  f=$(_write_script "bad.go" <<'SCRIPT'
package main

func process() {
    for _, item := range items {
        if err := validate(item); err != nil {
            if item.Required {
                fmt.Println("depth 3")
            }
        }
    }
}
SCRIPT
  )
  run "$VALIDATE_NESTING" "$f"
  [ "$status" -eq 1 ]
}

@test "validate-nesting: --max-depth overrides per-language default" {
  local f
  f=$(_write_script "deep_ok.go" <<'SCRIPT'
package main

func process() {
    for _, item := range items {
        if err := validate(item); err != nil {
            if item.Required {
                fmt.Println("depth 3 — allowed by override")
            }
        }
    }
}
SCRIPT
  )
  run "$VALIDATE_NESTING" --max-depth 3 "$f"
  [ "$status" -eq 0 ]
}

# ── CLI: diff mode ────────────────────────────────────────────────────────

_init_diff_repo() {
  local dir="$1"
  unset GIT_DIR GIT_WORK_TREE GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES 2>/dev/null || true
  GIT_CEILING_DIRECTORIES="$(dirname "$dir")"
  export GIT_CEILING_DIRECTORIES
  git -C "$dir" init -b main --quiet
  git -C "$dir" config user.email "test@example.com"
  git -C "$dir" config user.name "Test"
  git -C "$dir" config core.hooksPath /dev/null
}

@test "validate-nesting --diff: catches new violations in added lines" {
  _init_diff_repo "$TMPDIR"

  # Base commit: clean file
  cat > "$TMPDIR/script.sh" <<'SCRIPT'
#!/usr/bin/env bash
clean_func() {
  echo "ok"
}
SCRIPT
  git -C "$TMPDIR" add script.sh
  git -C "$TMPDIR" commit -m "init" --quiet
  git -C "$TMPDIR" tag base

  # Feature commit: add deeply nested function
  cat >> "$TMPDIR/script.sh" <<'SCRIPT'
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
  git -C "$TMPDIR" add script.sh
  git -C "$TMPDIR" commit -m "feat: add bad func" --quiet

  cd "$TMPDIR"
  run "$VALIDATE_NESTING" --diff base
  [ "$status" -eq 1 ]
  [[ "$output" == *"bad_func"* ]]
}

@test "validate-nesting --diff: ignores pre-existing violations" {
  _init_diff_repo "$TMPDIR"

  # Base commit: already has deep nesting
  cat > "$TMPDIR/script.sh" <<'SCRIPT'
#!/usr/bin/env bash
existing_bad() {
  if true; then
    if true; then
      if true; then
        echo "already deep"
      fi
    fi
  fi
}
SCRIPT
  git -C "$TMPDIR" add script.sh
  git -C "$TMPDIR" commit -m "init" --quiet
  git -C "$TMPDIR" tag base

  # Feature commit: only adds a clean function
  cat >> "$TMPDIR/script.sh" <<'SCRIPT'
clean_func() {
  echo "ok"
}
SCRIPT
  git -C "$TMPDIR" add script.sh
  git -C "$TMPDIR" commit -m "feat: add clean func" --quiet

  cd "$TMPDIR"
  run "$VALIDATE_NESTING" --diff base
  [ "$status" -eq 0 ]
}

@test "validate-nesting --diff: no changed files exits 0" {
  _init_diff_repo "$TMPDIR"

  cat > "$TMPDIR/script.sh" <<'SCRIPT'
#!/usr/bin/env bash
func() { echo "ok"; }
SCRIPT
  git -C "$TMPDIR" add script.sh
  git -C "$TMPDIR" commit -m "init" --quiet
  git -C "$TMPDIR" tag base

  cd "$TMPDIR"
  run "$VALIDATE_NESTING" --diff base
  [ "$status" -eq 0 ]
}

@test "validate-nesting --diff: skips deleted files" {
  _init_diff_repo "$TMPDIR"

  cat > "$TMPDIR/script.sh" <<'SCRIPT'
#!/usr/bin/env bash
func() {
  if true; then
    if true; then
      if true; then
        echo "deep"
      fi
    fi
  fi
}
SCRIPT
  git -C "$TMPDIR" add script.sh
  git -C "$TMPDIR" commit -m "init" --quiet
  git -C "$TMPDIR" tag base

  git -C "$TMPDIR" rm script.sh --quiet
  git -C "$TMPDIR" commit -m "remove script" --quiet

  cd "$TMPDIR"
  run "$VALIDATE_NESTING" --diff base
  [ "$status" -eq 0 ]
}

@test "validate-nesting --diff: composable with --max-depth" {
  _init_diff_repo "$TMPDIR"

  cat > "$TMPDIR/script.sh" <<'SCRIPT'
#!/usr/bin/env bash
func() { echo "ok"; }
SCRIPT
  git -C "$TMPDIR" add script.sh
  git -C "$TMPDIR" commit -m "init" --quiet
  git -C "$TMPDIR" tag base

  # Depth 3 — fails default (2) but passes --max-depth 3
  cat >> "$TMPDIR/script.sh" <<'SCRIPT'
deep_func() {
  if true; then
    if true; then
      if true; then
        echo "depth 3"
      fi
    fi
  fi
}
SCRIPT
  git -C "$TMPDIR" add script.sh
  git -C "$TMPDIR" commit -m "feat: depth 3" --quiet

  cd "$TMPDIR"
  run "$VALIDATE_NESTING" --diff base --max-depth 3
  [ "$status" -eq 0 ]
}

@test "validate-nesting --diff: mutually exclusive with positional files" {
  run "$VALIDATE_NESTING" --diff HEAD~1 somefile.sh
  [ "$status" -eq 2 ]
  [[ "$output" == *"mutually exclusive"* ]]
}

@test "validate-nesting --diff: unresolvable base ref exits 2 with no traceback" {
  _init_diff_repo "$TMPDIR"

  cat > "$TMPDIR/script.sh" <<'SCRIPT'
#!/usr/bin/env bash
func() { echo "ok"; }
SCRIPT
  git -C "$TMPDIR" add script.sh
  git -C "$TMPDIR" commit -m "init" --quiet

  cd "$TMPDIR"
  run "$VALIDATE_NESTING" --diff origin/HEAD
  [ "$status" -eq 2 ]
  [[ "$output" != *"Traceback"* ]]
  [[ "$output" == *"origin/HEAD"* ]]
}

@test "validate-nesting --diff: skips non-script files" {
  _init_diff_repo "$TMPDIR"

  echo "init" > "$TMPDIR/README.md"
  git -C "$TMPDIR" add README.md
  git -C "$TMPDIR" commit -m "init" --quiet
  git -C "$TMPDIR" tag base

  echo "updated" > "$TMPDIR/README.md"
  git -C "$TMPDIR" add README.md
  git -C "$TMPDIR" commit -m "update readme" --quiet

  cd "$TMPDIR"
  run "$VALIDATE_NESTING" --diff base
  [ "$status" -eq 0 ]
  [[ "$output" == *"0 files checked"* ]]
}

@test "validate-nesting: mixed bash, python, and go files" {
  local bash_f python_f go_f
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
  go_f=$(_write_script "ok.go" <<'SCRIPT'
package main

func ok() {
    if x > 0 {
        fmt.Println("ok")
    }
}
SCRIPT
  )
  run "$VALIDATE_NESTING" "$bash_f" "$python_f" "$go_f"
  [ "$status" -eq 0 ]
}
