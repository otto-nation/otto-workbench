#!/usr/bin/env bats
# Tests for herdr-app diff helpers (mapFiles, diffRows, patchRows) and worktree helpers.
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

# Evaluate EXPR with d (diff.js), w (worktree.js), g (github.js) in scope; print JSON.
_js() {
  run node --input-type=module -e "
    const d = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/diff.js');
    const w = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/worktree.js');
    const g = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/github.js');
    process.stdout.write(JSON.stringify($1));
  "
}

@test "mapFiles flattens slurped pages and maps status letters" {
  _js 'd.mapFiles([[{filename:"a",status:"added",additions:2,deletions:0,patch:"@@"}],[{filename:"b",previous_filename:"c",status:"renamed"},{filename:"x",status:"changed"}]]).map(f=>[f.path,f.previous,f.status,f.add,f.patch])'
  [ "$status" -eq 0 ]
  [ "$output" = '[["a",null,"A",2,"@@"],["b","c","R",0,null],["x",null,"M",0,null]]' ]
}

@test "mapFiles of junk is empty" {
  _js '[d.mapFiles(null), d.mapFiles({}), d.mapFiles([])]'
  [ "$output" = '[[],[],[]]' ]
}

@test "mapFiles numbers pages and reads has_patch" {
  _js 'd.mapFiles([[{filename:"a",has_patch:true},{filename:"b",has_patch:false}],[{filename:"c",patch:"@@"}]], 2).map(f=>[f.path,f.page,f.hasPatch,f.patch])'
  [ "$status" -eq 0 ]
  [ "$output" = '[["a",1,true,null],["b",1,false,null],["c",2,true,"@@"]]' ]
}

@test "mapFiles default page size" {
  _js '(()=>{const f=d.mapFiles(Array.from({length:101},(_, i)=>({filename:String(i)})));return [f[0].page,f[100].page]})()'
  [ "$status" -eq 0 ]
  [ "$output" = '[1,2]' ]
}

@test "diffRows numbers old and new sides from each hunk header" {
  _js 'd.diffRows(["@@ -5,2 +7,3 @@ f","-a"," b","+c","+","\\ No newline"]).map(r=>[r.kind,r.number,r.text])'
  [ "$output" = '[["@",null,"@@ -5,2 +7,3 @@ f"],["-",5,"a"],[" ",7,"b"],["+",8,"c"],["+",9," "],["",null,"\\ No newline"]]' ]
}

@test "patchRows caps lines and long lines and reports truncation" {
  _js '(()=>{const p=Array.from({length:1600},(_,i)=>"+"+(i===0?"x".repeat(4100):"l")).join("\n");const r=d.patchRows(p);return [r.rows.length,r.truncated,r.rows[0].text.endsWith("(line truncated)"),r.width>300]})()'
  [ "$output" = '[1500,true,true,true]' ]
}

@test "patchRows of a missing patch is empty and not truncated" {
  _js 'd.patchRows(null)'
  [ "$output" = '{"rows":[],"truncated":false,"width":0}' ]
}

@test "filesArgv puts per_page in the URL so gh does not POST" {
  _js 'g.filesArgv("o/n",7)'
  [[ "$output" == *'"/repos/o/n/pulls/7/files?per_page=100"'* ]]
  [[ "$output" != *'"-f"'* ]]
}

@test "parseSwitch reads path and action; bad output is a message" {
  _js '[w.parseSwitch("{\"action\":\"created\",\"path\":\"/p\"}"), w.parseSwitch("nope").ok, w.parseSwitch("{}").ok]'
  [ "$output" = '[{"ok":true,"path":"/p","created":true},false,false]' ]
}

@test "openForReviewState needs a local checkout and gives the reason" {
  _js '[w.openForReviewState({cwd:"/r",on:null}).enabled, w.openForReviewState({cwd:"/r",on:"m"}).reason, w.openForReviewState({cwd:null,on:null}).reason]'
  [ "$output" = '[true,"Open for Review works on this machine only","No local checkout of this repository"]' ]
}

@test "spaceFor matches a local space by folder only" {
  _js '[w.spaceFor([{id:"a",folder:"/p",machine:"m"},{id:"b",folder:"/p",machine:null}],"/p"), w.spaceFor([],"/p")]'
  [ "$output" = '["b",null]' ]
}

@test "attrArgvs chunks paths and generatedPaths reads set and true only" {
  _js '(()=>{const fs=Array.from({length:450},(_,i)=>({path:"f"+i}));const a=d.attrArgvs(fs);return [a.length,a[0].slice(0,4),a[2].length,[...d.generatedPaths("a\0linguist-generated\0set\0b\0linguist-generated\0unspecified\0c\0linguist-generated\0true\0d\0linguist-generated\0unset\0")]]})()'
  [ "$output" = '[3,["check-attr","-z","linguist-generated","--"],54,["a","c"]]' ]
}

@test "summarize leaves generated files out of every count and reports them apart" {
  _js 'd.summarize(d.markGenerated([{path:"a",add:3,del:1},{path:"g",add:100,del:50},{path:"b",add:2,del:0}],new Set(["g"])))'
  [ "$output" = '{"files":2,"add":5,"del":1,"generated":1}' ]
}

@test "patchRows width follows the glyph width it is given" {
  _js '[d.patchRows("+"+"x".repeat(100),7).width, d.patchRows("+"+"x".repeat(100),10).width]'
  [ "$output" = '[757,1060]' ]
}
