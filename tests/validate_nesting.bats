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

# ── CLI: Go support ────────────────────────────────────────────────────────

@test "validate-nesting: go file within default depth 3 exits 0" {
  local f
  f=$(_write_script "good.go" <<'SCRIPT'
package main

func process() {
    for _, item := range items {
        if err := validate(item); err != nil {
            if item.Required {
                fmt.Println("depth 3 — within Go default")
            }
        }
    }
}
SCRIPT
  )
  run "$VALIDATE_NESTING" "$f"
  [ "$status" -eq 0 ]
}

@test "validate-nesting: go file depth 4 violates default" {
  local f
  f=$(_write_script "bad.go" <<'SCRIPT'
package main

func process() {
    for _, item := range items {
        if err := validate(item); err != nil {
            if item.Required {
                for _, sub := range item.Subs {
                    fmt.Println("depth 4")
                }
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
                for _, sub := range item.Subs {
                    fmt.Println("depth 4 — allowed by override")
                }
            }
        }
    }
}
SCRIPT
  )
  run "$VALIDATE_NESTING" --max-depth 4 "$f"
  [ "$status" -eq 0 ]
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
