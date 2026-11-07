#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/scope.js — scope targets, merge and operator-facing errors.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js

setup() { load 'test_helper'; common_setup; }
teardown() { common_teardown; }

# $1 = JS expression over every scope.js export (as `m.<name>` or destructured below). Prints JSON.
_js() {
  run node --input-type=module -e "
    const m = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/scope.js');
    const { friendlyError, spaceTargets, mergeStates } = m;
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "friendlyError: null, auth, unreachable" {
  _js '({a:friendlyError(null),b:friendlyError({kind:"auth"}),c:friendlyError({kind:"auth"},"m"),d:friendlyError({kind:"unreachable"},"m")})'
  [ "$status" -eq 0 ]
  jq -e '.a=="" and .b=="run gh auth login on this Mac" and .c=="run gh auth login on m" and .d=="m is unreachable"' <<<"$output"
}

@test "friendlyError: not-a-repo, no-folder, unknown failed" {
  _js '({a:friendlyError({kind:"failed",message:"fatal: not a git repository (or any of the parent directories): .git"}),b:friendlyError({kind:"failed",message:"Not in a repository"}),c:friendlyError({kind:"failed",message:"not in a repository"}),d:friendlyError({kind:"failed",message:"boom"})})'
  [ "$status" -eq 0 ]
  jq -e '(.a|test("not in a git repository")) and (.b|test("has no folder")) and (.c|test("has no folder")) and .d=="boom"' <<<"$output"
}

@test "spaceTargets: skips empty/missing folder; machine undefined is on:null; null input" {
  _js '({a:spaceTargets([{folder:""},{machine:"x"},{folder:"/r"}]),b:spaceTargets(null)})'
  [ "$status" -eq 0 ]
  jq -e '(.a|length)==1 and .a[0].on==null and .a[0].cwd=="/r" and (.b|length)==0' <<<"$output"
}

@test "spaceTargets: same machine+folder is one; null vs machine are two; order kept" {
  _js 'spaceTargets([{folder:"/r"},{folder:"/r"},{machine:"m",folder:"/r"},{folder:"/s"}])'
  [ "$status" -eq 0 ]
  jq -e 'length==3 and .[0].on==null and .[0].cwd=="/r" and .[1].on=="m" and .[1].cwd=="/r" and .[2].cwd=="/s"' <<<"$output"
}

@test "mergeStates: two repos, first slug, each PR tagged, state order" {
  _js 'mergeStates([{repo:{slug:"a/b",host:"github.com"},prs:[{number:1}]},{repo:{slug:"c/d",host:"github.com"},prs:[{number:2}]}])'
  [ "$status" -eq 0 ]
  jq -e '.repos==2 and .slug=="a/b" and .prs[0].repoSlug=="a/b" and .prs[1].repoSlug=="c/d" and .prs[0].number==1 and .prs[1].number==2' <<<"$output"
}

@test "mergeStates: same host+slug keeps first state's PRs" {
  _js 'mergeStates([{repo:{slug:"a/b",host:"github.com"},prs:[{number:1}]},{repo:{slug:"a/b",host:"github.com"},prs:[{number:2}]}])'
  [ "$status" -eq 0 ]
  jq -e '.repos==1 and (.prs|length)==1 and .prs[0].number==1' <<<"$output"
}

@test "mergeStates: same slug different hosts are two repos" {
  _js 'mergeStates([{repo:{slug:"a/b",host:"github.com"},prs:[{number:1}]},{repo:{slug:"a/b",host:"ghe.example"},prs:[{number:2}]}])'
  [ "$status" -eq 0 ]
  jq -e '.repos==2' <<<"$output"
}

@test "mergeStates: empty prs still counts as a repo" {
  _js 'mergeStates([{repo:{slug:"a/b"},prs:[]}])'
  [ "$status" -eq 0 ]
  jq -e '.repos==1 and (.prs|length)==0' <<<"$output"
}

@test "mergeStates: missing repo or prs==null is skipped" {
  _js 'mergeStates([{prs:[{number:1}]},{repo:{slug:"a/b"},prs:null},{}])'
  [ "$status" -eq 0 ]
  jq -e '.repos==0' <<<"$output"
}

@test "mergeStates: me is the first non-empty login" {
  _js 'mergeStates([{repo:{slug:"a/b"},prs:[],me:""},{repo:{slug:"c/d"},prs:[],me:"bob"},{repo:{slug:"e/f"},prs:[],me:"alice"}])'
  [ "$status" -eq 0 ]
  jq -e '.me=="bob"' <<<"$output"
}

@test "mergeStates: does not mutate input PRs" {
  _js '(() => { const s={repo:{slug:"o/r"},prs:[{number:1}]}; mergeStates([s]); return s.prs[0].repoSlug === undefined; })()'
  [ "$status" -eq 0 ]
  jq -e '.' <<<"$output"
}
