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
    const { friendlyError, spaceTargets, mergeStates, skippable, boardState, repoId, representatives, staleKeys, cardId, REPO_PHASES, repoPhase } = m;
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "friendlyError: null, auth, timeout, exec" {
  _js '({a:friendlyError(null),b:friendlyError({kind:"auth"}),c:friendlyError({kind:"auth"},"m"),d:friendlyError({kind:"timeout",command:"pr review --list"},"m"),e:friendlyError({kind:"exec",command:"gh api /repos/o/n/pulls",message:""},"m"),f:friendlyError({kind:"exec",command:"git remote get-url",message:"boom"},"m")})'
  [ "$status" -eq 0 ]
  jq -e '.a=="" and .b=="run gh auth login on this Mac" and .c=="run gh auth login on m" and .d=="pr review --list took too long on m" and .e=="gh api /repos/o/n/pulls failed on m with no reason from herdr (often output over its size limit)" and .f=="git remote get-url failed on m: boom"' <<<"$output"
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

@test "mergeStates: repoSlugs keeps empty-prs repos, first-seen order, one per repoId" {
  _js 'mergeStates([{repo:{slug:"a/b",host:"github.com",on:null,cwd:"/a"},prs:[{number:1}]},{repo:{slug:"c/d",host:"github.com",on:null,cwd:"/c"},prs:[]},{repo:{slug:"a/b",host:"github.com",on:null,cwd:"/a2"},prs:[{number:9}]}])'
  [ "$status" -eq 0 ]
  jq -e '.repos==2 and (.repoSlugs|length)==2 and .repoSlugs[0]=="a/b" and .repoSlugs[1]=="c/d" and (.prs|length)==1' <<<"$output"
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

@test "friendlyError: missing-tool uses tools array, else not install" {
  _js '({a:friendlyError({kind:"missing-tool",tools:["pr","gh"],message:"pr, gh"}),b:friendlyError({kind:"missing-tool",message:"spawn ENOENT"})})'
  [ "$status" -eq 0 ]
  jq -e '.a=="install pr, gh on this Mac" and (.b|startswith("install")|not)' <<<"$output"
}

@test "friendlyError: not-a-repo hint only when scope is this" {
  _js '({a:friendlyError({kind:"failed",message:"fatal: not a git repository"},null,"all"),b:friendlyError({kind:"failed",message:"fatal: not a git repository"},null,"this")})'
  [ "$status" -eq 0 ]
  jq -e '(.a|test("All spaces")|not) and (.b|test("All spaces"))' <<<"$output"
}

@test "skippable: three skip messages, not auth/exec/missing-tool/exit 1" {
  _js '({a:skippable({kind:"failed",message:"fatal: not a git repository"}),b:skippable({kind:"failed",message:"Not in a repository"}),c:skippable({kind:"failed",message:"origin is not a GitHub remote"}),d:skippable({kind:"auth"}),e:skippable({kind:"exec",message:"not a git repository"}),f:skippable({kind:"missing-tool"}),g:skippable({kind:"failed",message:"exit 1"})})'
  [ "$status" -eq 0 ]
  jq -e '.a and .b and .c and (.d|not) and (.e|not) and (.f|not) and (.g|not)' <<<"$output"
}

@test "boardState: all-scope skips not-a-repo" {
  _js 'boardState([{cwd:"/a"},{cwd:"/tmp"}],[{repo:{slug:"o/r",host:"github.com"},prs:[{number:1}]},{error:{kind:"failed",message:"fatal: not a git repository"}}],"all")'
  [ "$status" -eq 0 ]
  jq -e '.skipped==1 and (.errors|length)==0 and .repos==1' <<<"$output"
}

@test "boardState: this-scope attributes not-a-repo to cwd" {
  _js 'boardState([{cwd:"/a"},{cwd:"/tmp"}],[{repo:{slug:"o/r",host:"github.com"},prs:[{number:1}]},{error:{kind:"failed",message:"fatal: not a git repository"}}],"this")'
  [ "$status" -eq 0 ]
  jq -e '.errors[0].cwd=="/tmp" and .skipped==0' <<<"$output"
}

@test "boardState: auth error uses the failing target machine" {
  _js 'boardState([{on:null,cwd:"/a"},{on:"m",cwd:"/b"}],[{repo:{slug:"o/r"},prs:[{number:1}]},{error:{kind:"auth"}}],"this")'
  [ "$status" -eq 0 ]
  jq -e '.errors[0].on=="m"' <<<"$output"
}

@test "boardState: missing state is loading" {
  _js 'boardState([{cwd:"/a"}],[],"this")'
  [ "$status" -eq 0 ]
  jq -e '.loading==1 and .repoStates[0].phase=="queued" and .repoStates[0].folder=="a"' <<<"$output"
}

@test "repoId: missing host is slash-slug" {
  _js 'repoId({slug:"o/r"})'
  [ "$status" -eq 0 ]
  jq -e '.=="/o/r"' <<<"$output"
}

@test "representatives: first per known repo; unknown all kept; order kept" {
  _js 'representatives([{n:1},{n:2},{n:3},{n:4}], (t) => t.n===1||t.n===2 ? "id" : (t.n===3 ? "other" : null))'
  [ "$status" -eq 0 ]
  jq -e 'length==3 and .[0].n==1 and .[1].n==3 and .[2].n==4' <<<"$output"
}

@test "staleKeys: keys not in keep" {
  _js 'staleKeys(["a","b","c"],["b"])'
  [ "$status" -eq 0 ]
  jq -e 'length==2 and .[0]=="a" and .[1]=="c"' <<<"$output"
}

@test "cardId: same number in two repos differs" {
  _js '({a:cardId({repoSlug:"a/b",number:12}),b:cardId({repoSlug:"c/d",number:12})})'
  [ "$status" -eq 0 ]
  jq -e '.a!=.b and (.a|test("#12$")) and (.b|test("#12$"))' <<<"$output"
}

@test "mergeStates: tags viewer from state me" {
  _js 'mergeStates([{repo:{slug:"a/b"},prs:[{number:1}],me:"bob"}])'
  [ "$status" -eq 0 ]
  jq -e '.prs[0].viewer=="bob"' <<<"$output"
}

@test "spaceTargets: pane cwds win over a stale space folder" {
  _js 'spaceTargets([{folder:"/stale",tabs:[{id:"t1"}]}],[{tab:"t1",cwd:"/live"}])'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].cwd=="/live" and .[0].on==null' <<<"$output"
}

@test "spaceTargets: no matching pane cwds falls back to space folder" {
  _js 'spaceTargets([{folder:"/space",tabs:[{id:"t1"}]}],[{tab:"other",cwd:"/live"}])'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].cwd=="/space"' <<<"$output"
}

@test "spaceTargets: same pane cwd is one; null cwd ignored; remote keeps machine" {
  _js 'spaceTargets([{folder:"/stale",machine:"m",tabs:[{id:"t1"},{id:"t2"}]}],[{tab:"t1",cwd:"/live"},{tab:"t1",cwd:"/live"},{tab:"t2",cwd:null}])'
  [ "$status" -eq 0 ]
  jq -e 'length==1 and .[0].cwd=="/live" and .[0].on=="m"' <<<"$output"
}

@test "boardState: an error carries the last successful load so the header can say how stale" {
  _js 'boardState([{cwd:"/a"},{cwd:"/b"}],[{error:{kind:"timeout"},updatedAt:1000,prs:[]},{error:{kind:"timeout"}}],"this").errors.map((e) => e.updatedAt)'
  [ "$status" -eq 0 ]
  [ "$output" = "[1000,null]" ]
}

@test "REPO_PHASES lists six phases; repoPhase passes hydrating through" {
  _js '({phases:REPO_PHASES,p:repoPhase({phase:"hydrating"})})'
  [ "$status" -eq 0 ]
  jq -e '.phases==["queued","loading","hydrating","loaded","failed","stale"] and .p=="hydrating"' <<<"$output"
}

@test "boardState: pulling is loading" {
  _js 'boardState([{cwd:"/a"}],[{pulling:true}],"this")'
  [ "$status" -eq 0 ]
  jq -e '.loading==1 and .repoStates[0].phase=="loading"' <<<"$output"
}

@test "boardState: loaded with partial true" {
  _js 'boardState([{on:null,cwd:"/work/box"}],[{repo:{slug:"acme/box"},prs:[],partial:true,updatedAt:5}],"this")'
  [ "$status" -eq 0 ]
  jq -e '.repoStates[0].phase=="loaded" and .repoStates[0].partial==true and .repoStates[0].folder=="box" and .repoStates[0].slug=="acme/box" and .repoStates[0].updatedAt==5 and .repoStates[0].error==null' <<<"$output"
}

@test "boardState: error with updatedAt is stale; without is failed" {
  _js 'boardState([{cwd:"/a"},{cwd:"/b"}],[{error:{kind:"timeout"},updatedAt:1000},{error:{kind:"timeout"}}],"this").repoStates.map((r) => r.phase)'
  [ "$status" -eq 0 ]
  [ "$output" = '["stale","failed"]' ]
}

@test "boardState: skippable target is absent from repoStates in all scope" {
  _js 'boardState([{cwd:"/a"},{cwd:"/tmp"}],[{repo:{slug:"o/r",host:"github.com"},prs:[{number:1}]},{error:{kind:"failed",message:"fatal: not a git repository"}}],"all")'
  [ "$status" -eq 0 ]
  jq -e '.skipped==1 and (.repoStates|length)==1 and .repoStates[0].phase=="loaded"' <<<"$output"
}

@test "boardState: two targets same repo is one repoStates entry" {
  _js 'boardState([{cwd:"/a"},{cwd:"/b"}],[{repo:{slug:"o/r",host:"github.com"},prs:[],partial:true},{repo:{slug:"o/r",host:"github.com"},prs:[],partial:false}],"this")'
  [ "$status" -eq 0 ]
  jq -e '(.repoStates|length)==1 and .repoStates[0].partial==true and .repoStates[0].folder=="a"' <<<"$output"
}

@test "boardState: st.phase hydrating is passed through" {
  _js 'boardState([{cwd:"/a"}],[{phase:"hydrating",prs:[]}],"this")'
  [ "$status" -eq 0 ]
  jq -e '.repoStates[0].phase=="hydrating"' <<<"$output"
}

@test "boardState: bogus st.phase is ignored" {
  _js '({a:boardState([{cwd:"/a"}],[{phase:"nope",prs:[]}],"this").repoStates[0].phase,b:boardState([{cwd:"/a"}],[{phase:"nope"}],"this").repoStates[0].phase})'
  [ "$status" -eq 0 ]
  jq -e '.a=="loaded" and .b=="queued"' <<<"$output"
}

@test "boardState: repoStates has no cwd key" {
  _js 'boardState([{cwd:"/a"}],[{repo:{slug:"o/r"},prs:[]}],"this")'
  [ "$status" -eq 0 ]
  jq -e '.repoStates[0]|has("cwd")|not' <<<"$output"
}

@test "friendlyError: a fourth argument names the local machine; three arguments still say this Mac" {
  _js '({a:friendlyError({kind:"auth"},null,"all","homelab · maximum"),b:friendlyError({kind:"missing-tool",tools:["gh"]},null,"all","box"),c:friendlyError({kind:"auth"},"m","all","box"),d:friendlyError({kind:"auth"},null,"all")})'
  [ "$status" -eq 0 ]
  jq -e '.a=="run gh auth login on homelab · maximum" and .b=="install gh on box" and .c=="run gh auth login on m" and .d=="run gh auth login on this Mac"' <<<"$output"
}

@test "boardState: repoStates carry the owed state, idle by default" {
  _js '[boardState([{cwd:"/a"}],[{repo:{slug:"o/a"},prs:[]}],"this").repoStates[0].owed, boardState([{cwd:"/a"}],[{repo:{slug:"o/a"},prs:[],owedState:"error",owedError:{kind:"failed",message:"m"}}],"this").repoStates[0].owed]'
  [ "$status" -eq 0 ]
  [ "$output" = '[{"state":"idle","error":null},{"state":"error","error":{"kind":"failed","message":"m"}}]' ]
}
