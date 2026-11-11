#!/usr/bin/env bats
# Tests for herdr-app include/exclude filters and the compact filter-bar model.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

_f() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/filters.js');
    process.stdout.write(JSON.stringify($1));
  "
}

@test "normalizeFilters: missing lists, mixed case/dupes, both sides, non-boolean flag, non-array list" {
  _f '(() => {
    const a = m.normalizeFilters({});
    const b = m.normalizeFilters({
      drafts: true,
      mineDrafts: "yes",
      repos: ["Acme/One", "acme/one", "ACME/TWO"],
      labels: "bug",
      authors: ["Ann", "bob", "ANN"],
      excludeRepos: ["acme/two"],
      excludeLabels: ["bug"],
      excludeAuthors: ["bob"],
    });
    return {
      missing: [a.repos, a.labels, a.authors, a.excludeRepos, a.excludeLabels, a.excludeAuthors, a.hideBots, a.drafts],
      flags: [b.drafts, b.mineDrafts],
      lists: [b.repos, b.labels, b.authors, b.excludeRepos, b.excludeLabels, b.excludeAuthors],
    };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"missing":[[],[],[],[],[],[],false,false],"flags":[true,false],"lists":[["acme/one"],[],["ann"],["acme/two"],["bug"],["bob"]]}' ]
}

@test "normalizeFilters: a value on both sides is exclude" {
  _f 'm.normalizeFilters({repos:["a/one","b/two"],excludeRepos:["A/One"]})'
  [ "$status" -eq 0 ]
  [ "$output" = '{"drafts":false,"mineDrafts":false,"landed":false,"empty":false,"hideBots":false,"repos":["b/two"],"labels":[],"authors":[],"excludeRepos":["a/one"],"excludeLabels":[],"excludeAuthors":[]}' ]
}

@test "filterAction: include on/off; include then not- moves and back; flag flip via default" {
  _f '(() => {
    const z = m.normalizeFilters({});
    const on = m.filterAction(z, "repo:Acme/One");
    const off = m.filterAction(on, "repo:acme/one");
    const moved = m.filterAction(on, "not-repo:acme/one");
    const back = m.filterAction(moved, "not-repo:acme/one");
    const viaInclude = m.filterAction(moved, "repo:acme/one");
    const drafts = m.filterAction(z, "drafts");
    const draftsOff = m.filterAction(drafts, "drafts");
    return {
      on: on.repos,
      off: off.repos,
      moved: [moved.repos, moved.excludeRepos],
      back: [back.repos, back.excludeRepos],
      viaInclude: [viaInclude.repos, viaInclude.excludeRepos],
      drafts: [drafts.drafts, draftsOff.drafts],
    };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"on":["acme/one"],"off":[],"moved":[[],["acme/one"]],"back":[[],[]],"viaInclude":[["acme/one"],[]],"drafts":[true,false]}' ]
}

@test "filterAction: label:branch-profile:emprise keeps the value after the first colon" {
  _f 'm.filterAction(m.normalizeFilters({}), "label:branch-profile:emprise").labels'
  [ "$status" -eq 0 ]
  [ "$output" = '["branch-profile:emprise"]' ]
}

@test "filterAction: unknown or empty value returns the same object; no mutation" {
  _f '(() => {
    const f = m.normalizeFilters({repos:["a/one"]});
    const snap = JSON.stringify(f);
    const same = [
      m.filterAction(f, "nope") === f,
      m.filterAction(f, "") === f,
      m.filterAction(f, "repo:") === f,
      m.filterAction(f, "clear:other") === f,
      m.filterAction(f, "weird:x") === f,
    ];
    m.filterAction(f, "repo:b/two");
    m.filterAction(f, "drafts");
    return { same, unchanged: JSON.stringify(f) === snap, repos: f.repos };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"same":[true,true,true,true,true],"unchanged":true,"repos":["a/one"]}' ]
}

@test "filterAction: clear:team empties all six lists and hideBots; clear:mine leaves authors" {
  _f '(() => {
    const dirty = m.normalizeFilters({
      drafts: true, landed: true, empty: true, hideBots: true,
      repos: ["a/one"], labels: ["bug"], authors: ["ann"],
      excludeRepos: ["b/two"], excludeLabels: ["docs"], excludeAuthors: ["bob"],
    });
    const team = m.filterAction(dirty, "clear:team");
    const mine = m.filterAction(dirty, "clear:mine");
    const batch = m.filterAction(dirty, "clear:batch");
    const keys = ["drafts","landed","empty","hideBots","repos","labels","authors","excludeRepos","excludeLabels","excludeAuthors"];
    const pick = (f) => keys.map((k) => f[k]);
    return { team: pick(team), mine: pick(mine), batch: pick(batch) };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"team":[true,true,true,false,[],[],[],[],[],[]],"mine":[true,true,true,true,[],[],["ann"],[],[],["bob"]],"batch":[true,true,true,true,[],[],["ann"],[],[],["bob"]]}' ]
}

@test "isBot: dependabot[bot] and Renovate[BOT] are bots; bot, botany, [bot]x are not" {
  _f '[m.isBot("dependabot[bot]"),m.isBot("Renovate[BOT]"),m.isBot("bot"),m.isBot("botany"),m.isBot("[bot]x"),m.isBot("alice")]'
  [ "$status" -eq 0 ]
  [ "$output" = '[true,true,false,false,false,false]' ]
}

@test "facetView: include/exclude per facet; exclude wins; stale include ignored; loaded empty repo; stale exclude no-op" {
  _f '(() => {
    const prs = [
      {n:1,repoSlug:"acme/one",labels:["bug"],author:"ann"},
      {n:2,repoSlug:"acme/two",labels:["docs"],author:"bob"},
      {n:3,repoSlug:"acme/one",labels:["docs"],author:"cam"},
      {n:4,repoSlug:"acme/two",labels:["bug"],author:"ann"},
    ];
    const ids = (f, extra={}) => m.facetView(prs, f, extra).list.map((p) => p.n);
    return {
      repoIn: ids({repos:["acme/one"]}),
      repoEx: ids({excludeRepos:["acme/one"]}),
      labelIn: ids({labels:["bug"]}),
      labelEx: ids({excludeLabels:["bug"]}),
      authorIn: ids({authors:["ann"]}),
      authorEx: ids({excludeAuthors:["ann"]}),
      both: ids({repos:["acme/one"],excludeRepos:["acme/one"]}),
      staleAuthor: ids({authors:["nobody"]}),
      staleLabel: ids({labels:["gone"]}),
      loadedEmpty: ids({repos:["acme/none"]},{loadedRepos:["acme/one","acme/two","acme/none"]}),
      staleEx: ids({excludeAuthors:["nobody"]}),
    };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"repoIn":[1,3],"repoEx":[2,4],"labelIn":[1,4],"labelEx":[2,3],"authorIn":[1,4],"authorEx":[2,3],"both":[2,4],"staleAuthor":[1,2,3,4],"staleLabel":[1,2,3,4],"loadedEmpty":[],"staleEx":[1,2,3,4]}' ]
}

@test "facetView: a stale author include is ignored" {
  _f 'm.facetView([{n:1,author:"ann"},{n:2,author:"bob"}],{authors:["carol"]},{tab:"team"}).list.map((p)=>p.n)'
  [ "$status" -eq 0 ]
  [ "$output" = '[1,2]' ]
}

@test "facetView: hideBots applies on Team only; mine/batch ignore authors and hideBots" {
  _f '(() => {
    const prs = [
      {n:1,author:"ann",repoSlug:"a/one",labels:[]},
      {n:2,author:"dependabot[bot]",repoSlug:"a/one",labels:[]},
      {n:3,author:"bob",repoSlug:"a/one",labels:[]},
    ];
    const ids = (tab, f) => m.facetView(prs, f, {tab, me:"ann"}).list.map((p) => p.n);
    const f = {authors:["bob"],hideBots:true};
    return { team: ids("team", f), mine: ids("mine", f), batch: ids("batch", f), teamBots: ids("team", {hideBots:true}) };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"team":[3],"mine":[1,2,3],"batch":[1,2,3],"teamBots":[1,3]}' ]
}

@test "facetView: viewer is stripped from author options and author includes" {
  _f '(() => {
    const prs = [{n:1,author:"Ann"},{n:2,author:"bob"},{n:3,author:"cam"}];
    const v = m.facetView(prs, {authors:["ANN","bob"]}, {tab:"team", me:"ann"});
    return { authors: v.options.authors, ids: v.list.map((p) => p.n) };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"authors":["bob","cam"],"ids":[2]}' ]
}

@test "facetView: options.labels are after the repo facet; options.authors are sorted without the viewer" {
  _f '(() => {
    const prs = [
      {n:1,repoSlug:"a/one",labels:["zeta"],author:"cam"},
      {n:2,repoSlug:"b/two",labels:["alpha"],author:"Ann"},
      {n:3,repoSlug:"a/one",labels:["beta"],author:"bob"},
    ];
    const v = m.facetView(prs, {repos:["a/one"]}, {me:"ann", authorsFrom: prs});
    return { labels: v.options.labels, authors: v.options.authors, ids: v.list.map((p) => p.n) };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"labels":["beta","zeta"],"authors":["bob","cam"],"ids":[1,3]}' ]
}

@test "barModel: facet keys per tab; batch Show is only Drafts with bots null; Repo hidden with one repo" {
  _f '(() => {
    const two = {repos:["acme/one","acme/two"],labels:["bug"],authors:["ann","bob"]};
    const keys = (tab, opts) => m.barModel({}, {tab, options: opts}).facets.map((f) => f.key);
    const batch = m.barModel({}, {tab:"batch", options: two});
    const one = keys("team", {repos:["acme/one"],labels:[],authors:[]});
    return {
      team: keys("team", two),
      mine: keys("mine", two),
      batchKeys: batch.facets.map((f) => f.key),
      batchRows: batch.facets[0].rows.map((r) => r.label),
      batchBots: batch.facets[0].bots,
      one,
    };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"team":["show","repos","labels","authors"],"mine":["show","repos","labels"],"batchKeys":["show","repos","labels"],"batchRows":["Drafts"],"batchBots":null,"one":["show","labels","authors"]}' ]
}

@test "barModel: search true at nine authors and false at eight" {
  _f '(() => {
    const eight = ["a","b","c","d","e","f","g","h"];
    const nine = [...eight, "i"];
    const s = (authors) => m.barModel({}, {tab:"team", options:{repos:[],labels:[],authors}}).facets.find((f) => f.key==="authors").search;
    return [s(eight), s(nine)];
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '[false,true]' ]
}

@test "barModel: pinned options sort first including a stale exclude" {
  _f 'm.barModel({authors:["bob"],excludeAuthors:["zoe"]},{tab:"team",options:{repos:[],labels:[],authors:["ann","bob","cam"]}}).facets.find((f)=>f.key==="authors").options.map((o)=>[o.value,o.state])'
  [ "$status" -eq 0 ]
  [ "$output" = '[["bob","include"],["zoe","exclude"],["ann",null],["cam",null]]' ]
}

@test "barModel: tag texts, clear null without tags, count, no digits in facet or row labels" {
  _f '(() => {
    const opts = {repos:["acme/box","acme/net"],labels:["bug"],authors:["ann"]};
    const dirty = m.barModel(
      {repos:["acme/box"],excludeLabels:["bug"],authors:["ann"],hideBots:true,drafts:true},
      {tab:"team", options: opts},
    );
    const clean = m.barModel({}, {tab:"team", options: opts});
    const labels = [];
    for (const f of dirty.facets) {
      labels.push(f.label);
      for (const r of f.rows ?? []) labels.push(r.label);
    }
    return {
      tags: dirty.tags.map((t) => t.text),
      clear: dirty.clear,
      count: dirty.count,
      cleanClear: clean.clear,
      cleanCount: clean.count,
      digits: labels.some((s) => /[0-9]/.test(s)),
    };
  })()'
  [ "$status" -eq 0 ]
  [ "$output" = '{"tags":["box","not bug","ann","Bots hidden"],"clear":"clear:team","count":5,"cleanClear":null,"cleanCount":0,"digits":false}' ]
}

@test "searchOptions: keeps pinned rows that miss the query" {
  _f 'm.searchOptions([{value:"ann",label:"ann",state:null},{value:"bob",label:"bob",state:"include"},{value:"cam",label:"cam",state:"exclude"}], "zzz").map((r)=>r.value)'
  [ "$status" -eq 0 ]
  [ "$output" = '["bob","cam"]' ]
}
