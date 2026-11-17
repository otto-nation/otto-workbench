# Shared helpers for the PR board web suites (tests/herdr_web_*.bats).
#
# _web JS — runs JS (statements, top-level await allowed) as an ES module under node, with:
#   W  absolute path of ai/herdr/web      L  absolute path of ai/herdr/app/mods/_lib
#   T  this test's scratch dir             out(v)  print JSON.stringify(v)
#   fakeExec(table) an exec answering from rows {prefix, code, stdout, stderr, reject}, matched by
#                   prefix against "cmd arg1 arg2…"; unmatched calls answer code 1 "unexpected …"
#   calls  every call fakeExec saw: {cmd, args, cwd, on, timeout}
# The JS is passed in the environment (WEB_BODY) and the wrapper script on stdin, never interpolated
# and never as a command-line argument, so it needs no shell escaping. A long argv entry is not safe:
# on macOS 27.2 node is SIGKILLed (status 137) when it starts through a symlinked path (a mise shim,
# a Homebrew link) with one argument over about 975 characters, which failed every long case here.

WEB_FAKE_EXEC='
const calls = [];
const fakeExec = (table) => async (cmd, args, opts = {}) => {
  calls.push({ cmd, args: [...args], cwd: opts.cwd ?? null, on: opts.on ?? null, timeout: opts.timeout ?? null });
  const key = [cmd, ...args].join(" ");
  for (const row of table) {
    if (!key.startsWith(row.prefix)) continue;
    if (row.reject) throw new Error(row.reject);
    const stdout = typeof row.stdout === "string" ? row.stdout : JSON.stringify(row.stdout ?? "");
    return { code: row.code ?? 0, stdout, stderr: row.stderr ?? "" };
  }
  return { code: 1, stdout: "", stderr: "unexpected " + key };
};'

_web() {
  local script
  script="
    const W = '$REPO_ROOT/ai/herdr/web';
    const L = '$REPO_ROOT/ai/herdr/app/mods/_lib';
    const T = '$BATS_TEST_TMPDIR';
    const out = (v) => process.stdout.write(JSON.stringify(v));
    $WEB_FAKE_EXEC
    const body = new (async () => {}).constructor('W', 'L', 'T', 'out', 'fakeExec', 'calls', process.env.WEB_BODY);
    await body(W, L, T, out, fakeExec, calls);
  "
  run env WEB_BODY="$1" node --input-type=module - <<< "$script"
}
