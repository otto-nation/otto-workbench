#!/usr/bin/env bats
# Tests for ai/pi/extensions/container-context — the backstop that loads a
# worktree's context file into a Pi session that started at a bare-repo
# container. detect.ts imports node built-ins only, so node loads it directly.

bats_require_minimum_version 1.5.0

setup() {
  load 'test_helper'
  common_setup
  # Physical path: git and resolve-worktree report /private/var on macOS.
  TMPDIR="$(cd "$BATS_TEST_TMPDIR" && pwd -P)"
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  DETECT="$REPO_ROOT/ai/pi/extensions/container-context/detect.ts"
  INDEX="$REPO_ROOT/ai/pi/extensions/container-context/index.ts"
  SEED="$TMPDIR/seed"
  CONTAINER="$TMPDIR/container"
}

teardown() {
  common_teardown
}

_make_seed() {
  git init -q --initial-branch=main "$SEED"
  git -C "$SEED" config user.email test@example.com
  git -C "$SEED" config user.name Test
  printf 'seed\n' > "$SEED/README.md"
  git -C "$SEED" add -A
  git -C "$SEED" commit -qm init
}

# _make_container [--no-worktree] — container/.git bare, worktree on main beside it.
_make_container() {
  mkdir -p "$CONTAINER"
  git clone -q --bare "$SEED" "$CONTAINER/.git"
  [[ "${1:-}" == --no-worktree ]] && return 0
  git -C "$CONTAINER" worktree add -q "$CONTAINER/main" main
}

# _context DIR [RESOLVER] — prints containerContext(DIR) as JSON.
_context() {
  run node --input-type=module -e "
    const { containerContext } = await import('$DETECT');
    const r = process.argv[2] ? containerContext(process.argv[1], process.argv[2]) : containerContext(process.argv[1]);
    process.stdout.write(JSON.stringify(r));
  " -- "$1" "${2:-}"
}

# _render DIR KIND [LOADED] — prints sectionFor or noticeFor (KIND) for DIR's
# context, with LOADED (comma-separated paths) as the files the extension added.
_render() {
  run node --input-type=module -e "
    const m = await import('$DETECT');
    const c = m.containerContext(process.argv[1]);
    const loaded = process.argv[3] ? process.argv[3].split(',') : [];
    process.stdout.write(c ? m[process.argv[2]](c, loaded) : 'NULL');
  " -- "$1" "$2" "${3:-}"
}

# ── Resolution ───────────────────────────────────────────────────────────────

@test "a container resolves to its worktree" {
  _make_seed
  _make_container

  _context "$CONTAINER"
  [ "$status" -eq 0 ]
  [[ "$output" == *"\"kind\":\"resolved\""* ]]
  [[ "$output" == *"\"worktree\":\"$CONTAINER/main\""* ]]
}

@test "a worktree, an ordinary repo and a loose directory are left alone" {
  _make_seed
  _make_container
  mkdir -p "$TMPDIR/loose"

  local dir
  for dir in "$CONTAINER/main" "$SEED" "$TMPDIR/loose"; do
    _context "$dir"
    [ "$status" -eq 0 ]
    [ "$output" = null ]
  done
}

@test "a container with no worktree is reported with the resolver's reason" {
  _make_seed
  _make_container --no-worktree

  _context "$CONTAINER"
  [ "$status" -eq 0 ]
  [[ "$output" == *"\"kind\":\"unresolved\""* ]]
  [[ "$output" == *"no worktree on 'main'"* ]]
}

@test "a resolver that cannot run is reported at a container, not taken as 'not a container'" {
  # The wrapper in front asks the same script, so treating a missing resolver
  # as "nothing to do" would switch both layers off at once.
  _make_seed
  _make_container

  _context "$CONTAINER" "$TMPDIR/no-such-resolver"
  [ "$status" -eq 0 ]
  [[ "$output" == *"\"kind\":\"unresolved\""* ]]
  [[ "$output" == *"resolve-worktree could not run"* ]]
}

@test "a resolver that cannot run stays silent outside a container" {
  _make_seed

  _context "$SEED" "$TMPDIR/no-such-resolver"
  [ "$status" -eq 0 ]
  [ "$output" = null ]
}

@test "a resolver that succeeds with no path is unresolved, not a worktree of ''" {
  # An empty worktree would make worktreeFiles match every absolute path.
  _make_seed
  _make_container
  printf '#!/bin/sh\nexit 0\n' > "$TMPDIR/empty-resolver"
  chmod +x "$TMPDIR/empty-resolver"

  _context "$CONTAINER" "$TMPDIR/empty-resolver"
  [ "$status" -eq 0 ]
  [[ "$output" == *"\"kind\":\"unresolved\""* ]]
  [[ "$output" == *"printed no worktree path"* ]]
}

@test "the resolver is found when the checkout path has a space in it" {
  # new URL(...).pathname keeps %20, naming a path that does not exist.
  local root="$TMPDIR/a checkout"
  mkdir -p "$root/ai/pi/extensions/container-context" "$root/bin"
  cp "$DETECT" "$root/ai/pi/extensions/container-context/detect.ts"

  run node --input-type=module -e "
    const { pathToFileURL } = await import('node:url');
    const m = await import(pathToFileURL(process.argv[1]).href);
    process.stdout.write(m.RESOLVE_WORKTREE);
  " -- "$root/ai/pi/extensions/container-context/detect.ts"
  [ "$status" -eq 0 ]
  [ "$output" = "$root/bin/resolve-worktree" ]
}

@test "an inherited GIT_DIR does not redirect the answer" {
  # Hooks export GIT_DIR; git then skips discovery and answers for the hook's repo.
  _make_seed
  _make_container

  GIT_DIR="$SEED/.git" _context "$CONTAINER"
  [[ "$output" == *"\"worktree\":\"$CONTAINER/main\""* ]]
}

@test "the resolver is found relative to the extension, not on PATH" {
  _make_seed
  _make_container

  # node, git and bash stay reachable; the workbench's bin/ does not.
  #
  # node's directory comes from process.execPath, not `command -v`: a version
  # manager's shim resolves node by searching the rest of PATH and the cwd's
  # config, and from the temp container with PATH narrowed it finds neither.
  # bash's comes from $BASH because the resolver needs 4.3+, and on macOS the
  # /bin/bash that /usr/bin:/bin leaves is 3.2.
  local node_dir
  node_dir="$(dirname "$(node --input-type=module -e 'process.stdout.write(process.execPath)')")"
  PATH="$node_dir:$(dirname "$BASH"):$(dirname "$(command -v git)"):/usr/bin:/bin" _context "$CONTAINER"
  [[ "$output" == *"\"kind\":\"resolved\""* ]]
}

# ── The context files ────────────────────────────────────────────────────────

# _pi_package — the installed Pi SDK's directory, or empty. The managed install
# keeps it under the agent dir's current release; an npm-global install under
# `npm root -g`. CI has neither, so the cases that need Pi skip there.
_pi_package() {
  local launcher agent version
  launcher="$(command -v pi 2>/dev/null)" || true
  if [[ -n "$launcher" ]]; then
    agent="$(dirname "$(dirname "$launcher")")"
    version="$(cat "$agent/install/current-version" 2>/dev/null)" || true
    local managed="$agent/install/releases/$version/node_modules/@earendil-works/pi-coding-agent"
    [[ -n "$version" && -f "$managed/package.json" ]] && { printf '%s' "$managed"; return 0; }
  fi
  local global
  global="$(npm root -g 2>/dev/null)/@earendil-works/pi-coding-agent"
  [[ -f "$global/package.json" ]] && printf '%s' "$global"
  return 0
}

# _files JSON WORKTREE — prints worktreeFiles(JSON, WORKTREE) paths.
_files() {
  run node --input-type=module -e "
    const { worktreeFiles } = await import('$DETECT');
    process.stdout.write(worktreeFiles(JSON.parse(process.argv[1]), process.argv[2]).map(f => f.path).join(','));
  " -- "$1" "$2"
}

@test "only files inside the worktree are added — the session has the rest" {
  _files '[{"path":"/h/.pi/agent/AGENTS.md","content":""},{"path":"/r/c/main/CLAUDE.md","content":""}]' /r/c/main
  [ "$status" -eq 0 ]
  [ "$output" = /r/c/main/CLAUDE.md ]
}

@test "a sibling directory sharing the worktree's prefix is not inside it" {
  _files '[{"path":"/r/c/main-old/CLAUDE.md","content":""}]' /r/c/main
  [ "$status" -eq 0 ]
  [ "$output" = "" ]
}

@test "Pi's own loader, run for the worktree, yields the file the extension adds" {
  # The extension adds what loadProjectContextFiles returns for the worktree.
  # This asks the installed SDK, so a Pi upgrade that changes the names or the
  # order changes what is added rather than leaving a stale copy behind.
  local pkg
  pkg="$(_pi_package)"
  [[ -n "$pkg" ]] || skip "pi not installed — nothing to ask about context files"
  _make_seed
  _make_container
  printf 'claude\n' > "$CONTAINER/main/CLAUDE.md"
  printf 'agents\n' > "$CONTAINER/main/AGENTS.md"
  mkdir -p "$TMPDIR/agent"

  run node --input-type=module -e "
    const { loadProjectContextFiles } = await import('$pkg/dist/index.js');
    const { containerContext, worktreeFiles } = await import('$DETECT');
    const c = containerContext(process.argv[1]);
    const all = loadProjectContextFiles({ cwd: c.worktree, agentDir: process.argv[2] });
    process.stdout.write(worktreeFiles(all, c.worktree).map(f => f.path).join(','));
  " -- "$CONTAINER" "$TMPDIR/agent"
  [ "$status" -eq 0 ]
  [ "$output" = "$CONTAINER/main/AGENTS.md" ]
}

# ── What the session is told ─────────────────────────────────────────────────

@test "the section names the worktree, what loaded, and what could not" {
  _make_seed
  _make_container

  _render "$CONTAINER" sectionFor "$CONTAINER/main/CLAUDE.md"
  [[ "$output" == *"started at $CONTAINER, a bare-repo container"* ]]
  [[ "$output" == *"loaded below as project instructions: $CONTAINER/main/CLAUDE.md."* ]]
  [[ "$output" == *".pi/"* ]]
  [[ "$output" == *"git status"* ]]
  [[ "$output" == *"Run git and repository commands in $CONTAINER/main"* ]]
}

@test "an unresolved container's section says nothing was loaded" {
  _make_seed
  _make_container --no-worktree

  _render "$CONTAINER" sectionFor
  [[ "$output" == *"no worktree could be resolved"* ]]
  [[ "$output" == *"None of the repository's context files were loaded"* ]]
}

@test "a worktree with no context file is said to have none" {
  _make_seed
  _make_container

  _render "$CONTAINER" sectionFor
  [[ "$output" == *"has no context file"* ]]
}

@test "the notice names the container and what loaded" {
  _make_seed
  _make_container

  _render "$CONTAINER" noticeFor "$CONTAINER/main/CLAUDE.md"
  [[ "$output" == "pi: started at bare container $CONTAINER — loaded $CONTAINER/main/CLAUDE.md"* ]]
}

@test "the section name is one Pi accepts" {
  run node --input-type=module -e "
    const { SECTION_NAME } = await import('$DETECT');
    process.stdout.write(String(/^[a-z][a-z0-9_-]*\$/.test(SECTION_NAME) && SECTION_NAME !== 'preamble'));
  "
  [ "$output" = true ]
}

# ── Wiring ───────────────────────────────────────────────────────────────────

@test "the extension mutates the prompt options and never replaces the prompt" {
  run grep -q 'pi.on("before_agent_start"' "$INDEX"
  [ "$status" -eq 0 ]
  run grep -q 'contextFiles.push' "$INDEX"
  [ "$status" -eq 0 ]
  run grep -q 'loadProjectContextFiles' "$INDEX"
  [ "$status" -eq 0 ]
  # Neither a returned `systemPrompt:` nor an assigned `systemPrompt =`.
  run grep -qE 'systemPrompt[[:space:]]*(:|=[^=])' "$INDEX"
  [ "$status" -ne 0 ]
}

@test "detect.ts loads without the Pi SDK" {
  run grep -q '@earendil-works/pi-coding-agent' "$DETECT"
  [ "$status" -ne 0 ]
}
