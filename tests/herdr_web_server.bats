#!/usr/bin/env bats
# Integration: ai/herdr/web/server/main.js under Bun, with stub tools on PATH (spec § 8.2, § 8.5).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js

TOK='0123456789abcdef0123456789abcdef'

setup() {
  load 'test_helper'
  common_setup
  # shellcheck source=../lib/portable.sh
  source "$REPO_ROOT/lib/portable.sh"
  STUBS="$BATS_TEST_TMPDIR/stubs"
  mkdir -p "$STUBS"
  printf '#!/usr/bin/env bash\necho %s\n' "'{\"terminals\":[]}'" > "$STUBS/hc"
  for t in gh git pr wt; do printf '#!/usr/bin/env bash\nexit 1\n' > "$STUBS/$t"; done
  chmod +x "$STUBS"/*
  BUN="$(command -v bun)"
}

teardown() {
  [[ -n "${SERVER_PID:-}" ]] && kill "$SERVER_PID" 2>/dev/null
  common_teardown
}

# _start [ENV...] — starts the server on a free port; sets PORT from its start line.
_start() {
  env -i HOME="$BATS_TEST_TMPDIR/home" PATH="$STUBS:/usr/bin:/bin" PR_BOARD_TOKEN="$TOK" PR_BOARD_PORT=0 \
    PR_BOARD_STATE_DIR="$BATS_TEST_TMPDIR/state" "$@" \
    "$BUN" "$REPO_ROOT/ai/herdr/web/server/main.js" > "$BATS_TEST_TMPDIR/server.log" 2>&1 3>&- &
  SERVER_PID=$!
  for _ in $(seq 1 50); do
    PORT="$(sed -n 's/.*listening on http:\/\/127\.0\.0\.1:\([0-9]*\).*/\1/p' "$BATS_TEST_TMPDIR/server.log")"
    [[ -n "$PORT" ]] && return 0
    sleep 0.1
  done
  cat "$BATS_TEST_TMPDIR/server.log"
  return 1
}

@test "server: refuses to start without a token (exit 2, message names the variable)" {
  run env -i HOME="$BATS_TEST_TMPDIR" PATH="$STUBS:/usr/bin:/bin" PR_BOARD_PORT=0 "$BUN" "$REPO_ROOT/ai/herdr/web/server/main.js"
  [ "$status" -eq 2 ]
  [[ "$output" == *"pr-board: PR_BOARD_TOKEN"* ]]
}

@test "server: health without a token; 401 for tokenless loopback on / and /api/board" {
  _start
  run curl -fsS "http://127.0.0.1:$PORT/healthz"
  [ "$status" -eq 0 ]
  [ "$output" = '{"ok":true,"version":"dev"}' ]
  run curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/"
  [ "$output" = "401" ]
  run curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/api/board"
  [ "$output" = "401" ]
}

@test "server: sign in, read an empty board, refresh needs Origin" {
  _start
  run curl -s -o /dev/null -w '%{http_code}' -c "$BATS_TEST_TMPDIR/jar" -H "Origin: http://127.0.0.1:$PORT" \
    --data-urlencode "token=$TOK" "http://127.0.0.1:$PORT/login"
  [ "$output" = "303" ]
  run curl -fsS -b "$BATS_TEST_TMPDIR/jar" "http://127.0.0.1:$PORT/api/board"
  [ "$status" -eq 0 ]
  jq -e '.prs==[] and .version=="dev"' <<<"$output"
  run curl -s -o /dev/null -w '%{http_code}' -b "$BATS_TEST_TMPDIR/jar" -X POST -H 'content-type: application/json' -d '{}' "http://127.0.0.1:$PORT/api/refresh"
  [ "$output" = "403" ]
  run curl -s -o /dev/null -w '%{http_code}' -b "$BATS_TEST_TMPDIR/jar" -X POST -H 'content-type: application/json' \
    -H "Origin: http://127.0.0.1:$PORT" -d '{}' "http://127.0.0.1:$PORT/api/refresh"
  [ "$output" = "202" ]
}

@test "server: the board page and its CSS load with the bearer token; the state dir is created 0700" {
  _start PR_BOARD_NAME="homelab · maximum"
  run curl -fsS -H "Authorization: Bearer $TOK" "http://127.0.0.1:$PORT/"
  [ "$status" -eq 0 ]
  [[ "$output" == *"homelab · maximum"* ]]
  [[ "$output" == *'media="(max-width: 559.98px)"'* ]]
  [[ "$output" == *'media="(min-width: 1100px)"'* ]]
  run curl -fsS -H "Authorization: Bearer $TOK" "http://127.0.0.1:$PORT/gen/theme.css"
  [[ "$output" == *"--canvas:"* ]]
  run curl -fsS "http://127.0.0.1:$PORT/login.css"
  [[ "$output" == *"--canvas:"* && "$output" == *".login"* ]]
  # file_mode (lib/portable.sh) prints octal permission bits, e.g. 700; stat format flags are banned.
  run file_mode "$BATS_TEST_TMPDIR/state"
  [ "$output" = "700" ]
}

@test "server: SIGTERM exits 0" {
  _start
  kill -TERM "$SERVER_PID"
  # wait must run in this shell (the server is its child), not in run's subshell.
  local rc=0
  wait "$SERVER_PID" || rc=$?
  SERVER_PID=
  [ "$rc" -eq 0 ]
}
