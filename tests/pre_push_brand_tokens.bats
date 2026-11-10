#!/usr/bin/env bats
# Tests for the pre-push hook filling the brand-token cache before
# `dev-deps --check`.
#
# The fetch and the check are invoked as `$REPO_ROOT/bin/local/...`, so a PATH
# stub cannot intercept them. A fixture repo hosts those two stubs (and the
# libs the hook sources); gitleaks is stubbed on PATH so the scan above the
# fetch succeeds and the run stops at --check rather than the full gate.
bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup

  HOOK="$REPO_ROOT/git/hooks/pre-push-workbench"
  FIXTURE="$TMPDIR/repo"
  CALLS="$TMPDIR/calls"
  FETCHED="$TMPDIR/fetched"
  FETCH_FAIL="$TMPDIR/fetch-fail"
  : > "$CALLS"

  git init -q -b main "$FIXTURE"
  ln -s "$REPO_ROOT/lib" "$FIXTURE/lib"
  mkdir -p "$FIXTURE/bin/local"

  BIN="$TMPDIR/bin"
  mkdir -p "$BIN"
  printf '%s\n' '#!/usr/bin/env bash' 'exit 0' > "$BIN/gitleaks"
  chmod +x "$BIN/gitleaks"

  # Fails --offline until a non-offline --resolve-brand has run. A fetch that
  # is asked to fail never writes FETCHED, so --offline stays red — the same
  # shape as an empty state-dir cache after a brand bump.
  cat > "$FIXTURE/bin/local/generate-herdr-app-themes" <<EOF
#!/usr/bin/env bash
printf 'generate-herdr-app-themes %s\n' "\$*" >> "$CALLS"
offline=0
resolve=0
for arg in "\$@"; do
  [[ "\$arg" == --offline ]] && offline=1
  [[ "\$arg" == --resolve-brand ]] && resolve=1
done
if (( offline )); then
  [[ -f "$FETCHED" ]] && exit 0
  echo "offline: no cache" >&2
  exit 1
fi
if (( resolve )); then
  if [[ -f "$FETCH_FAIL" ]]; then
    echo "fetch failed" >&2
    exit 1
  fi
  touch "$FETCHED"
  exit 0
fi
exit 0
EOF
  chmod +x "$FIXTURE/bin/local/generate-herdr-app-themes"

  cat > "$FIXTURE/bin/local/dev-deps" <<EOF
#!/usr/bin/env bash
printf 'dev-deps %s\n' "\$*" >> "$CALLS"
if [[ "\${1:-}" == --check ]]; then
  echo "Missing development dependencies:" >&2
  echo "  otto brand tokens (herdr-app theme) — run: bin/local/dev-deps (or: npm --prefix site ci)" >&2
  exit 1
fi
exit 0
EOF
  chmod +x "$FIXTURE/bin/local/dev-deps"
}

teardown() {
  common_teardown
}

# _run_hook — the real pre-push hook, with cwd the fixture so REPO_ROOT is it.
_run_hook() {
  PATH="$BIN:$PATH" run bash -c 'cd "$1" && exec "$2"' _ "$FIXTURE" "$HOOK"
}

@test "the hook fetches brand tokens before check, and still refuses when the fetch fails" {
  touch "$FETCH_FAIL"
  _run_hook
  [ "$status" -ne 0 ]
  [[ "$output" == *"Install them with: task dev:setup (bin/local/dev-deps)"* ]]
  [[ "$output" == *"otto brand tokens (herdr-app theme)"* ]]
  [[ "$output" != *"pre-push hook failed"* ]]

  local first second
  first="$(sed -n '1p' "$CALLS")"
  second="$(sed -n '2p' "$CALLS")"
  [ "$first" = "generate-herdr-app-themes --resolve-brand" ]
  [ "$second" = "dev-deps --check" ]
  [[ "$first" != *"--offline"* ]]
}
