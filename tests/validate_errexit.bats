#!/usr/bin/env bats
# Tests for validate-errexit Python script — detects dangerous && patterns
# that silently exit under set -e when they are the last statement before
# a scope closer (}, fi, done, esac, ;;).

setup() {
  load 'test_helper'
  common_setup
  TMPDIR="$(mktemp -d)"
  VALIDATE_ERREXIT="$REPO_ROOT/bin/local/validate-errexit"
}

teardown() {
  rm -rf "$TMPDIR"
  common_teardown
}

_py() {
  python3 -c "
import sys, importlib.util, importlib.machinery
loader = importlib.machinery.SourceFileLoader('ve', '$VALIDATE_ERREXIT')
spec = importlib.util.spec_from_loader('ve', loader)
mod = importlib.util.module_from_spec(spec)
sys.modules['ve'] = mod
spec.loader.exec_module(mod)
$1
"
}

_write_script() {
  local name="$1"
  local path="$TMPDIR/$name"
  cat > "$path"
  echo "$path"
}

# ── has_unguarded_and ────────────────────────────────────────────────────────

@test "has_unguarded_and: detects [[ ]] && cmd" {
  result=$(_py "print(mod.has_unguarded_and('[[ -f x ]] && echo hi'))")
  [ "$result" = "True" ]
}

@test "has_unguarded_and: ignores line with || fallback" {
  result=$(_py "print(mod.has_unguarded_and('[[ -f x ]] && echo hi || true'))")
  [ "$result" = "False" ]
}

@test "has_unguarded_and: ignores line without &&" {
  result=$(_py "print(mod.has_unguarded_and('echo hello'))")
  [ "$result" = "False" ]
}

@test "has_unguarded_and: ignores if condition" {
  result=$(_py "print(mod.has_unguarded_and('if [[ -f x ]] && [[ -d y ]]; then'))")
  [ "$result" = "False" ]
}

@test "has_unguarded_and: ignores elif condition" {
  result=$(_py "print(mod.has_unguarded_and('elif [[ -f x ]] && [[ -d y ]]; then'))")
  [ "$result" = "False" ]
}

@test "has_unguarded_and: ignores while condition" {
  result=$(_py "print(mod.has_unguarded_and('while [[ -f x ]] && read -r line; do'))")
  [ "$result" = "False" ]
}

# ── check_file: safe patterns ───────────────────────────────────────────────

@test "check_file: && not last statement — no violation" {
  local f
  f=$(_write_script "safe.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  [[ -f x ]] && echo "found"
  echo "always runs"
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "0" ]
}

@test "check_file: && with || fallback — no violation" {
  local f
  f=$(_write_script "fallback.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  [[ -f x ]] && echo "found" || true
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "0" ]
}

@test "check_file: if/then/fi — no violation" {
  local f
  f=$(_write_script "if_then.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  if [[ -f x ]]; then
    echo "found"
  fi
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "0" ]
}

@test "check_file: return 0 after && — no violation" {
  local f
  f=$(_write_script "return_guard.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  [[ -f x ]] && echo "found"
  return 0
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "0" ]
}

@test "check_file: && inside heredoc — no violation" {
  local f
  f=$(_write_script "heredoc.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  cat <<EOF
[[ -f x ]] && echo "inside heredoc"
EOF
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "0" ]
}

# ── check_file: dangerous patterns ──────────────────────────────────────────

@test "check_file: && before } — violation" {
  local f
  f=$(_write_script "func_end.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  [[ -f x ]] && echo "found"
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "1" ]
}

@test "check_file: && before fi — violation" {
  local f
  f=$(_write_script "before_fi.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  if true; then
    [[ -f x ]] && echo "found"
  fi
  echo "ok"
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "1" ]
}

@test "check_file: && before done — violation" {
  local f
  f=$(_write_script "before_done.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  for f in *.sh; do
    [[ -f "$f" ]] && echo "found"
  done
  echo "ok"
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "1" ]
}

@test "check_file: && before esac — violation" {
  local f
  f=$(_write_script "before_esac.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  case "$1" in
    a) echo "ok" ;;
    *) [[ -f x ]] && echo "found" ;;
  esac
  echo "ok"
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "1" ]
}

@test "check_file: && on same line as ;; — violation" {
  local f
  f=$(_write_script "inline_case.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  case "$1" in
    a) [[ -f x ]] && echo "found" ;;
  esac
  echo "ok"
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "1" ]
}

@test "check_file: && in else branch before fi — violation" {
  local f
  f=$(_write_script "else_branch.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  if true; then
    echo "ok"
  else
    [[ -f x ]] && echo "found"
  fi
  echo "ok"
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "1" ]
}

@test "check_file: cmd && cmd (not just [[ ]]) before } — violation" {
  local f
  f=$(_write_script "cmd_and.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  awk '1' file > tmp && mv tmp file
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "1" ]
}

@test "check_file: multiple violations in one file" {
  local f
  f=$(_write_script "multi.sh" <<'SCRIPT'
#!/usr/bin/env bash
func_a() {
  [[ -f x ]] && echo "a"
}
func_b() {
  [[ -f y ]] && echo "b"
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "2" ]
}

@test "check_file: violation reports function name" {
  local f
  f=$(_write_script "named.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_special_func() {
  [[ -f x ]] && echo "found"
}
SCRIPT
  )
  result=$(_py "
v = mod.check_file('$f')
print(v[0][2])
")
  [ "$result" = "my_special_func" ]
}

@test "check_file: && at EOF (top-level, no closer) — violation" {
  local f
  f=$(_write_script "eof.sh" <<'SCRIPT'
#!/usr/bin/env bash
[[ -f x ]] && echo "found"
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "1" ]
}

@test "check_file: continuation lines are joined" {
  local f
  f=$(_write_script "continuation.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  [[ -f x ]] && \
    echo "found"
}
SCRIPT
  )
  result=$(_py "print(len(mod.check_file('$f')))")
  [ "$result" = "1" ]
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

@test "discover_scripts: excludes .git directory" {
  mkdir -p "$TMPDIR/repo/.git/hooks"
  printf '#!/usr/bin/env bash\necho hi\n' > "$TMPDIR/repo/.git/hooks/pre-commit.sh"
  result=$(_py "print(len(mod.discover_scripts('$TMPDIR/repo')))")
  [ "$result" = "0" ]
}

# ── CLI ──────────────────────────────────────────────────────────────────────

@test "validate-errexit --help exits 0" {
  run "$VALIDATE_ERREXIT" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"scope closer"* ]]
}

@test "validate-errexit: passing file exits 0" {
  local f
  f=$(_write_script "good.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  if [[ -f x ]]; then
    echo "found"
  fi
}
SCRIPT
  )
  run "$VALIDATE_ERREXIT" "$f"
  [ "$status" -eq 0 ]
}

@test "validate-errexit: failing file exits 1" {
  local f
  f=$(_write_script "bad.sh" <<'SCRIPT'
#!/usr/bin/env bash
bad_func() {
  [[ -f x ]] && echo "found"
}
SCRIPT
  )
  run "$VALIDATE_ERREXIT" "$f"
  [ "$status" -eq 1 ]
}

@test "validate-errexit: --quiet suppresses passing files" {
  local f
  f=$(_write_script "good.sh" <<'SCRIPT'
#!/usr/bin/env bash
my_func() {
  echo "ok"
}
SCRIPT
  )
  run "$VALIDATE_ERREXIT" --quiet "$f"
  [ "$status" -eq 0 ]
  [[ "$output" != *"good.sh"* ]]
}
