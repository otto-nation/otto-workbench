# Loads a herdr-app mod view under node with herdr's host modules stubbed, for the suites that pin
# a view's behaviour (tests/herdr_app_mods_prs_*.bats).
#
# _host_setup — assembles $BATS_TEST_TMPDIR/host/prs the way _herdr_app_install_mods lays a mod out
#   (prs/*.js, prs/_lib, prs/_ui) and writes node loader hooks that resolve `gpui-kit`, `gpui-base`
#   and `herdr` to generated stubs: every gpui name is a chainable no-op (`View` a class); a herdr
#   name calls globalThis.__herdr[name] when the test set it, else is a no-op.
# _host JS — runs JS (top-level await allowed) as an ES module with:
#   M  the assembled mod dir       out(v)  print JSON.stringify(v)
#   H  herdr state: H.state (shared keys), H.spacesList, H.panesList, H.focusedPane, and any herdr
#      function to override (H.exec, H.watchFolder, …)
#   calls        every exec call: {label, cwd, on}
#   label(c, a)  pr:<phase>[:offline][:stamp] | tools | hc:<sub>[:label] — a remote call is unwrapped
#   fakePr(doc, answer?) an exec answering the tools check, hc ls and `pr board` from `doc`;
#                answer(label, opts) may return {code, stdout, stderr} (or a promise of one) first
#   fakeCx()     {cx, timers, settle()} — cx.timer.every/after record, cx.spawn runs and is awaited by settle
# The JS is passed as argv, never interpolated, so it needs no shell escaping.

HOST_HOOKS='
import fs from "node:fs";
import path from "node:path";
const HOST = new Set(["gpui-kit", "gpui-base", "herdr"]);
const names = {};
export async function initialize({ root }) {
  const re = /import\s*\{([^}]*)\}\s*from\s*["\x27](gpui-kit|gpui-base|herdr)["\x27]/gs;
  for (const dir of [root, path.join(root, "_ui")]) {
    for (const f of fs.readdirSync(dir).filter((n) => n.endsWith(".js")).sort()) {
      for (const m of fs.readFileSync(path.join(dir, f), "utf8").matchAll(re)) {
        const set = (names[m[2]] ??= new Set());
        for (const raw of m[1].split(",")) { const n = raw.trim().split(/\s+as\s+/)[0]; if (n) set.add(n); }
      }
    }
  }
}
const CHAIN = "const chain = () => { const f = new Proxy(function () {}, { get: (t, k) => (k === \"then\" ? undefined : k === \"prototype\" ? t.prototype : () => f), apply: () => f, construct: () => f }); return f; };\n";
function source(spec) {
  const list = [...(names[spec] ?? [])];
  if (spec === "herdr") return CHAIN + list.map((n) => `export const ${n} = (...a) => (globalThis.__herdr?.${n} ? globalThis.__herdr.${n}(...a) : chain());`).join("\n");
  return CHAIN + list.map((n) => (n === "View" ? "export class View {}" : `export const ${n} = chain();`)).join("\n");
}
export async function resolve(spec, ctx, next) {
  return HOST.has(spec) ? { url: `stub:///${spec}`, shortCircuit: true } : next(spec, ctx);
}
export async function load(url, ctx, next) {
  return url.startsWith("stub:///") ? { format: "module", source: source(url.slice(8)), shortCircuit: true } : next(url, ctx);
}'

HOST_PRELUDE='
const H = (globalThis.__herdr = {
  state: {}, spacesList: [], panesList: [], focusedPane: null,
  shared: (k) => H.state[k], setShared: (k, v) => { H.state[k] = v; },
  spaces: () => H.spacesList, panes: () => H.panesList, focused: () => H.focusedPane,
  props: () => ({}), layout: () => ({}), watchFolder: () => Promise.reject(new Error("no watch in tests")),
});
const calls = [];
const label = (cmd, args) => {
  let c = cmd, a = args;
  // Remote wrap: preamble is `sh -c SCRIPT sh CMD ...`; isolate inserts seconds, MemoryMax, scope|plain before CMD.
  if (c === "sh" && a[2] === "sh") {
    if (a[5] === "scope" || a[5] === "plain") { c = a[6]; a = a.slice(7); }
    else { c = a[3]; a = a.slice(4); }
  }
  if (c === "sh") return "tools";
  if (c === "hc") return "hc:" + a[0] + (a.includes("--label") ? ":label" : "");
  if (c === "pr") return "pr:" + a[a.indexOf("--phase") + 1] + (a.includes("--offline") ? ":offline" : "") + (a.includes("--if-changed") ? ":stamp" : "");
  return [c, ...a].join(" ");
};
const fakePr = (doc, answer = () => null) => async (cmd, args, opts = {}) => {
  const l = label(cmd, args);
  calls.push({ label: l, cwd: opts.cwd ?? null, on: opts.on ?? null });
  const custom = await answer(l, opts);
  if (custom) return custom;
  if (l === "tools") return { code: 0, stdout: "/b/pr\n/b/gh\n/b/wt\n", stderr: "" };
  if (l.startsWith("hc:ls")) return { code: 0, stdout: "{\"terminals\":[]}", stderr: "" };
  if (l.startsWith("pr:")) { const phase = l.split(":")[1]; return { code: 0, stdout: JSON.stringify({ ...doc, phase, stamp: "st-" + phase }), stderr: "" }; }
  return { code: 1, stdout: "", stderr: "unexpected " + l };
};
const fakeCx = () => {
  const timers = []; const tasks = [];
  const cx = {
    timer: { every: (ms, fn) => timers.push({ kind: "every", ms, fn }), after: (ms, fn) => timers.push({ kind: "after", ms, fn }) },
    spawn: (fn) => { const p = Promise.resolve().then(() => fn(cx)); tasks.push(p); return p; },
    notify: () => {},
  };
  return { cx, timers, settle: async () => { while (tasks.length) await tasks.shift(); } };
};'

_host_setup() {
  HOST_DIR="$BATS_TEST_TMPDIR/host"
  local mods="$REPO_ROOT/ai/herdr/app/mods"
  # The loader hooks need node:module register() (node >= 20.6). Say so, rather than failing at import.
  node -e 'const [a, b] = process.versions.node.split(".").map(Number); process.exit(a > 20 || (a === 20 && b >= 6) ? 0 : 1)' \
    || { echo "node $(node -p process.versions.node): the host harness needs node >= 20.6 (node:module register)" >&2; return 1; }
  mkdir -p "$HOST_DIR"
  cp -R "$mods/prs" "$HOST_DIR/prs"
  cp -R "$mods/_lib" "$HOST_DIR/prs/_lib"
  cp -R "$mods/_ui" "$HOST_DIR/prs/_ui"
  printf '%s\n' "$HOST_HOOKS" > "$HOST_DIR/hooks.mjs"
  printf '%s\n' "import { register } from 'node:module';" \
    "register('file://$HOST_DIR/hooks.mjs', { data: { root: '$HOST_DIR/prs' } });" > "$HOST_DIR/register.mjs"
  printf '%s\n' "$HOST_PRELUDE" > "$HOST_DIR/prelude.js"
}

_host() {
  # The prelude's quotes cannot sit inside a shell -e string (plan's _host interpolates
  # HOST_PRELUDE into double quotes). Same names, argv body, and register() hooks; the
  # runner file is the equivalent of that -e script. --no-deprecation: node 26 prints
  # DEP0205 for module.register on stderr, and bats run merges stderr into $output.
  {
    printf 'const M = %s;\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$HOST_DIR/prs")"
    printf 'const FIX = %s;\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$REPO_ROOT/tests/fixtures/board_document.json")"
    printf '%s\n' 'const out = (v) => process.stdout.write(JSON.stringify(v));'
    cat "$HOST_DIR/prelude.js"
    printf '%s\n' \
      "const body = new (async () => {}).constructor('M', 'FIX', 'out', 'H', 'calls', 'label', 'fakePr', 'fakeCx', process.argv[2]);" \
      "await body(M, FIX, out, H, calls, label, fakePr, fakeCx);"
  } > "$HOST_DIR/run.mjs"
  run node --no-deprecation --import "file://$HOST_DIR/register.mjs" "$HOST_DIR/run.mjs" "$1"
}
