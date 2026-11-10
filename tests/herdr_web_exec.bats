#!/usr/bin/env bats
# Tests for ai/herdr/web/server/exec.js — the herdr exec contract on a server (real processes).
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/web/* ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  load 'herdr_web_helper'
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}
teardown() { common_teardown; }

@test "exec: exit code, stdout and stderr come back apart" {
  _web "const { makeExec } = await import(W + '/server/exec.js');
        out(await makeExec()('sh', ['-c', 'echo out; echo err >&2; exit 3'], {}));"
  [ "$status" -eq 0 ]
  [ "$output" = '{"code":3,"stdout":"out\n","stderr":"err\n"}' ]
}

@test "exec: a program that cannot start rejects (board_data classifies it missing-tool)" {
  _web "const { makeExec } = await import(W + '/server/exec.js');
        try { await makeExec()('no-such-program-pr-board', [], {}); out('resolved'); } catch (e) { out(e.code); }"
  [ "$status" -eq 0 ]
  [ "$output" = '"ENOENT"' ]
}

@test "exec: a missing cwd answers code 1 instead of a misleading ENOENT" {
  _web "const { makeExec } = await import(W + '/server/exec.js');
        out(await makeExec()('true', [], { cwd: T + '/gone' }));"
  [ "$status" -eq 0 ]
  jq -e '.code==1 and (.stderr|test("not a directory"))' <<<"$output"
}

@test "exec: a timeout kills the process group and answers 124" {
  _web "const { makeExec } = await import(W + '/server/exec.js');
        const t0 = Date.now();
        const r = await makeExec()('sh', ['-c', 'sleep 5 & sleep 5'], { timeout: 300 });
        out({ code: r.code, took: Date.now() - t0 < 3000, says: /took longer than/.test(r.stderr) });"
  [ "$status" -eq 0 ]
  [ "$output" = '{"code":124,"took":true,"says":true}' ]
}

@test "exec: children never see HC_TERMINAL_ID or PR_BOARD_*; prompts are disabled" {
  _web "const { makeExec } = await import(W + '/server/exec.js');
        const exec = makeExec({ env: { PATH: process.env.PATH, HC_TERMINAL_ID: 't9', PR_BOARD_TOKEN: 'secret', KEEP: 'k' } });
        out((await exec('sh', ['-c', 'echo \${HC_TERMINAL_ID-u}:\${PR_BOARD_TOKEN-u}:\$KEEP:\$GH_PROMPT_DISABLED:\$GIT_TERMINAL_PROMPT'], {})).stdout);"
  [ "$status" -eq 0 ]
  [ "$output" = '"u:u:k:1:0\n"' ]
}

@test "exec: hc is the configured binary; remote calls go through hc exec --json and decode base64" {
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  cat > "$BATS_TEST_TMPDIR/bin/fake-hc" <<'EOF'
#!/usr/bin/env bash
printf '%s\n' "$*" >> "$(dirname "$0")/argv.log"
if [[ "$1" == exec ]]; then
  printf '{"code":0,"ms":1,"signal":null,"stderr":"","stdout":"%s","timed_out":false,"truncated":false}\n' "$(printf hi | base64)"
else
  echo "local $*"
fi
EOF
  chmod +x "$BATS_TEST_TMPDIR/bin/fake-hc"
  _web "const { makeExec } = await import(W + '/server/exec.js');
        const exec = makeExec({ hcBin: T + '/bin/fake-hc' });
        const a = await exec('hc', ['ls', '--json'], {});
        const b = await exec('git', ['status'], { on: 'box', cwd: '/w', timeout: 45000 });
        out([a.stdout, b]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["local ls --json\n",{"code":0,"stdout":"hi","stderr":""}]' ]
  run cat "$BATS_TEST_TMPDIR/bin/argv.log"
  [ "${lines[1]}" = "exec --json --on box --timeout 45 --cwd /w -- git status" ]
}

@test "exec: a remote timeout or an hc error rejects (board_data classifies timeout/exec)" {
  mkdir -p "$BATS_TEST_TMPDIR/bin"
  printf '#!/usr/bin/env bash\necho %s\n' "'{\"code\":null,\"stderr\":\"\",\"stdout\":\"\",\"timed_out\":true}'" > "$BATS_TEST_TMPDIR/bin/slow-hc"
  printf '#!/usr/bin/env bash\necho %s >&2\nexit 3\n' "'{\"error\":{\"kind\":\"not_found\",\"message\":\"no machine box\"}}'" > "$BATS_TEST_TMPDIR/bin/bad-hc"
  chmod +x "$BATS_TEST_TMPDIR/bin/slow-hc" "$BATS_TEST_TMPDIR/bin/bad-hc"
  _web "const { makeExec } = await import(W + '/server/exec.js');
        const msg = async (bin) => { try { await makeExec({ hcBin: T + '/bin/' + bin })('gh', ['api'], { on: 'box' }); return 'resolved'; } catch (e) { return e.message; } };
        out([await msg('slow-hc'), await msg('bad-hc')]);"
  [ "$status" -eq 0 ]
  jq -e '(.[0]|test("took longer than")) and (.[1]|test("no machine box"))' <<<"$output"
}

@test "exec: at most maxProcs children run at once" {
  _web "const { makeExec } = await import(W + '/server/exec.js');
        const exec = makeExec({ maxProcs: 2 });
        const t0 = Date.now();
        await Promise.all([1, 2, 3, 4].map(() => exec('sh', ['-c', 'sleep 0.3'], {})));
        out(Date.now() - t0 >= 550);"
  [ "$status" -eq 0 ]
  [ "$output" = 'true' ]
}

@test "readTextFile: inside the root reads; outside, a symlink out, or no known root refuses; missing is null" {
  mkdir -p "$BATS_TEST_TMPDIR/reviews/r1" "$BATS_TEST_TMPDIR/elsewhere"
  echo "# review" > "$BATS_TEST_TMPDIR/reviews/r1/review.md"
  echo "secret" > "$BATS_TEST_TMPDIR/elsewhere/x.md"
  ln -s "$BATS_TEST_TMPDIR/elsewhere/x.md" "$BATS_TEST_TMPDIR/reviews/r1/link.md"
  _web "const { makeExec, makeReadTextFile } = await import(W + '/server/exec.js');
        const read = makeReadTextFile({ exec: makeExec(), rootFor: (on) => (on == null ? T + '/reviews' : null) });
        const r = async (p, o) => { try { return await read(p, o); } catch (e) { return 'refused'; } };
        out([await r(T + '/reviews/r1/review.md'), await r(T + '/reviews/r1/../../elsewhere/x.md'),
             await r(T + '/reviews/r1/link.md'), await r(T + '/reviews/r1/missing.md'), await r(T + '/reviews/r1/review.md', { on: 'box' })]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["# review\n","refused","refused",null,"refused"]' ]
}

@test "readTextFile: a remote path is normalised, checked against that machine's root, then read with cat" {
  _web "const { makeReadTextFile } = await import(W + '/server/exec.js');
        const exec = fakeExec([{ prefix: 'cat /r/x/review.md', stdout: 'remote text' }]);
        const read = makeReadTextFile({ exec, rootFor: (on) => (on === 'box' ? '/r' : null) });
        const r = async (p) => { try { return await read(p, { on: 'box' }); } catch (e) { return 'refused'; } };
        out([await r('/r/x/../x/review.md'), await r('/r/../etc/passwd'), await r('/rx/y'), calls.map((c) => [c.cmd, c.on])]);"
  [ "$status" -eq 0 ]
  [ "$output" = '["remote text","refused","refused",[["cat","box"]]]' ]
}
