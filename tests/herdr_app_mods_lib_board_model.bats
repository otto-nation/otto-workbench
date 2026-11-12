#!/usr/bin/env bats
# _lib/board_model.js: lanes, facets, chips and lane Batch presets for both PR board views.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js tests/fixtures/board_document.json

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  LIB="$REPO_ROOT/ai/herdr/app/mods/_lib"
  FIX="$REPO_ROOT/tests/fixtures/board_document.json"
}
teardown() { common_teardown; }

# $1: a script body with m/c/f/cm/bl/d/doc/rows/kit/view.
# Body is argv, not interpolated into -e: the plan's double-quoted literals cannot live in a
# double-quoted node -e (Task 2/3 harness ruling). node --no-deprecation: DEP0205 on node 26.
_model() {
  local runjs="$BATS_TEST_TMPDIR/model-run.mjs"
  {
    printf 'import fs from "node:fs";\n'
    printf 'const m = await import(%s);\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$LIB/board_model.js")"
    printf 'const c = await import(%s);\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$LIB/columns.js")"
    printf 'const f = await import(%s);\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$LIB/filters.js")"
    printf 'const cm = await import(%s);\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$LIB/compact.js")"
    printf 'const bl = await import(%s);\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$LIB/batch_launch.js")"
    printf 'const d = await import(%s);\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$LIB/board_doc.js")"
    printf 'const doc = JSON.parse(fs.readFileSync(%s, "utf8"));\n' "$(node --no-deprecation -p 'JSON.stringify(process.argv[1])' "$FIX")"
    printf '%s\n' \
      'const rows = doc.rows.map(d.rowToPr).map((p) => ({ ...p, repoSlug: "acme/box", viewer: doc.viewer }));' \
      'const kit = rows.map((p) => ({ ...p, repoSlug: "acme/kit", number: p.number + 100, labels: ["infra"], author: p.number === 8 ? "dependabot[bot]" : p.author }));' \
      'const view = { prs: [...rows, ...kit], me: doc.viewer, repoSlugs: ["acme/kit", "acme/box"], columns: doc.columns };' \
      "const body = new (async () => {}).constructor('m', 'c', 'f', 'cm', 'bl', 'd', 'doc', 'rows', 'kit', 'view', process.argv[2]);" \
      'await body(m, c, f, cm, bl, d, doc, rows, kit, view);'
  } > "$runjs"
  run node --no-deprecation "$runjs" "$1"
}

@test "board model: equals today's mod derivation for every tab, scope and filter set" {
  _model '
    const oracle = (state, filters, tab, scope) => {
      const me = state.me ?? "";
      const base = tab === "mine" ? state.prs.filter((p) => c.isMine(p, me) && (c.filterOn(filters, "mineDrafts") || !p.isDraft)) : c.filterPrs(state.prs, filters, me);
      const fv = f.facetView(base, filters, { tab, me, loadedRepos: state.repoSlugs, authorsFrom: state.prs });
      const mine = tab === "mine";
      const docSpec = c.columnsFor(state.columns, mine ? "mine" : "team");
      const spec = docSpec.length ? docSpec : mine ? c.MINE_COLUMNS : c.TEAM_COLUMNS;
      const again = mine ? fv.list.filter((p) => c.isMine(p, me) && (c.filterOn(filters, "mineDrafts") || !p.isDraft)) : c.filterPrs(fv.list, filters, me);
      const shown = c.visibleColumns(c.groupColumns(again, (p) => c.laneOf(p, mine ? "mine" : "team"), spec), c.filterOn(filters, "empty"));
      const sections = cm.compactSections(c.groupColumns(fv.list, (p) => c.laneOf(p, mine ? "mine" : "team"), spec));
      const repos = scope === "all" ? [...state.repoSlugs].sort() : [];
      const bar = f.barModel(filters, { tab, options: { repos, labels: fv.options.labels, authors: fv.options.authors } });
      return { columns: shown.map((x) => [x.key, x.items.map((p) => p.number), mine ? bl.columnPreset(x) : null]),
        sections: sections.map((x) => [x.key, x.count, mine ? bl.columnPreset(x) : null]), labels: fv.options.labels, authors: fv.options.authors, bar };
    };
    const project = (x, mine) => ({ columns: x.columns.map((l) => [l.key, l.items.map((p) => p.number), l.batch]),
      sections: x.sections.map((s) => [s.key, s.count, s.batch]), labels: x.options.labels, authors: x.options.authors, bar: x.bar });
    const sets = [{}, { drafts: true }, { landed: true }, { empty: true }, { mineDrafts: true }, { hideBots: true },
      { labels: ["comments"] }, { excludeLabels: ["infra"] }, { repos: ["acme/kit"] }, { excludeAuthors: ["you"] }, { authors: ["pat"] }];
    const bad = [];
    // A mine draft so default Mine vs mineDrafts:true actually diverge (fixture drafts are not is_mine).
    const drafted = { ...view, prs: [...view.prs, { ...rows[0], number: 14, isDraft: true }] };
    for (const raw of sets) for (const tab of ["team", "mine"]) for (const scope of ["this", "all"]) {
      const filters = f.normalizeFilters(raw);
      const want = JSON.stringify(oracle(drafted, filters, tab, scope));
      const got = JSON.stringify(project(m.boardModel(drafted, { tab, filters, scope }), tab === "mine"));
      if (want !== got) bad.push({ raw, tab, scope, want, got });
    }
    const noDoc = { ...view, columns: undefined };
    const fallback = m.boardModel(noDoc, { tab: "mine", filters: f.normalizeFilters({}), scope: "all" }).spec.map((s) => s.key);
    process.stdout.write(JSON.stringify({ bad, fallback: fallback.join(",") === c.MINE_COLUMNS.map((s) => s.key).join(",") }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"bad":[],"fallback":true}' ]
}

@test "board model: Team by default lists open non-draft PRs by lane; labels are offered; no batch presets" {
  _model '
    const x = m.boardModel({ ...view, prs: rows, repoSlugs: ["acme/box"] }, { tab: "team", filters: f.normalizeFilters({}), scope: "this" });
    process.stdout.write(JSON.stringify({ cols: x.columns.map((l) => l.key + ":" + l.items.map((p) => p.number) + ":" + l.batch), labels: x.options.labels, repos: x.options.repos, count: x.filterCount }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"cols":["open:8:null","reviewed:7:null"],"labels":["comments"],"repos":[],"count":0}' ]
}

@test "board model: Mine puts owed feedback in Has Comments with its Batch preset, on lanes and sections" {
  _model '
    const x = m.boardModel({ ...view, prs: rows, repoSlugs: ["acme/box"] }, { tab: "mine", filters: f.normalizeFilters({}), scope: "all" });
    process.stdout.write(JSON.stringify({ cols: x.columns.map((l) => [l.key, l.items.map((p) => p.number), l.batch]), secs: x.sections.map((s) => [s.key, s.count, s.batch]), lanes: x.lanes.length }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"cols":[["has-comments",[7],{"steps":["comments"],"ai":true}]],"secs":[["has-comments",1,{"steps":["comments"],"ai":true}]],"lanes":9}' ]
}

@test "board model: mineOpen lists the viewer's open PRs, drafts included, merged and others' excluded" {
  _model '
    const prs = [...rows, { ...rows[0], number: 11, isDraft: true }, { ...rows[0], number: 12, state: "CLOSED" },
      { ...rows[0], number: 13, state: "MERGED" }];
    process.stdout.write(JSON.stringify(m.mineOpen(prs, doc.viewer).map((p) => p.number)));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[7,11]' ]
}

@test "board model: wantFor asks for owed on Mine only" {
  _model 'process.stdout.write(JSON.stringify([m.wantFor("mine"), m.wantFor("team"), m.wantFor(undefined)]));'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"owed":true},{"owed":false},{"owed":false}]' ]
}

@test "board model: Has Comments is loading until owed lands; the lane stays shown with no Batch, other lanes ready" {
  _model '
    const rs = (owed) => [{ slug: "acme/box", on: null, phase: "loaded", error: null, owed }];
    const mk = (owed, prs = rows) => m.boardModel({ ...view, prs, repoSlugs: ["acme/box"], repoStates: rs(owed) }, { tab: "mine", filters: f.normalizeFilters({}), scope: "this" });
    const pick = (x) => { const l = x.lanes.find((y) => y.key === "has-comments"); return [l.status, l.message, l.batch, x.columns.some((c) => c.key === "has-comments"), x.sections.some((s) => s.key === "has-comments")]; };
    const empty = mk({ state: "loading", error: null }, rows.filter((p) => p.number !== 7));
    const awaiting = mk({ state: "idle", error: null }).lanes.find((l) => l.key === "awaiting-review").status;
    process.stdout.write(JSON.stringify({ idle: pick(mk({ state: "idle", error: null })), ready: pick(mk({ state: "ready", error: null })), empty: pick(empty), awaiting }));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '{"idle":["loading","Loading comments…",null,true,true],"ready":["ready",null,{"steps":["comments"],"ai":true},true,true],"empty":["loading","Loading comments…",null,true,true],"awaiting":"ready"}' ]
}

@test "board model: an owed failure is the lane's error in words; a repo with its own error is not counted" {
  _model '
    const at = (repoStates) => m.boardModel({ ...view, prs: rows, repoSlugs: ["acme/box", "acme/kit"], repoStates }, { tab: "mine", filters: f.normalizeFilters({}), scope: "all" }).lanes.find((l) => l.key === "has-comments");
    const ok = { slug: "acme/kit", on: null, error: null, owed: { state: "ready", error: null } };
    const bad = { slug: "acme/box", on: null, error: null, owed: { state: "error", error: { kind: "failed", message: "boom" } } };
    const words = { ...bad, owed: { state: "error", error: "already in words" } };
    const broken = { slug: "acme/box", on: null, error: { kind: "failed", message: "x" }, owed: { state: "idle", error: null } };
    process.stdout.write(JSON.stringify([[at([bad]).status, at([bad]).message], at([ok, bad]).message, at([words]).message, at([ok, broken]).status]));
  '
  [ "$status" -eq 0 ]
  [ "$output" = '[["error","Could not load comments: boom"],"Could not load comments for acme/box: boom","Could not load comments: already in words","ready"]' ]
}
