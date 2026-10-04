#!/usr/bin/env bats
# Tests for the Pi superpowers-bootstrap extension.
setup() {
  load 'test_helper'
  common_setup

  export HOME="$TMPDIR/home"
  export WORKBENCH_CONFIG_DIR="$TMPDIR/config"
  export WORKBENCH_SYNC=true
  mkdir -p "$HOME"

  # Around 150 cases below evaluate a guard predicate by spawning `node` to
  # import one .ts module, and nearly all of the ~82ms that costs is startup
  # and type-stripping rather than the predicate. Node's compile cache makes
  # that work survive across processes, taking it to ~59ms.
  #
  # $BATS_FILE_TMPDIR, not $TMPDIR: common_setup pins the latter per test, so a
  # cache written there is discarded before the next case can read it, and the
  # first-run cost would be paid every time. bats removes the file-level
  # directory when the file finishes, so nothing outlives the run.
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
}

teardown() {
  common_teardown
}

# ─── superpowers-bootstrap ────────────────────────────────────────────────
# Delivers the superpowers bootstrap as a system-prompt section. bootstrap.ts
# imports only node built-ins, so node loads it directly.

# _section SKILLS_JSON — prints bootstrapSection(SKILLS) as JSON (null or text).
_section() {
  run node --input-type=module -e "
    const { bootstrapSection } = await import('$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap/bootstrap.ts');
    process.stdout.write(JSON.stringify(bootstrapSection(JSON.parse(process.argv[1]))));
  " -- "$1"
}

@test "superpowers-bootstrap: the loaded skill's body becomes the section" {
  mkdir -p "$TMPDIR/sp"
  printf -- '---\nname: using-superpowers\ndescription: x\n---\n\nUse skills first.\n' > "$TMPDIR/sp/SKILL.md"

  _section "[{\"name\":\"other\",\"filePath\":\"/nope\"},{\"name\":\"using-superpowers\",\"filePath\":\"$TMPDIR/sp/SKILL.md\"}]"
  [ "$status" -eq 0 ]
  [[ "$output" == *'Use skills first.'* ]]
  [[ "$output" != *'description: x'* ]]
}

@test "superpowers-bootstrap: the section carries Pi's subagent and task-list mapping" {
  mkdir -p "$TMPDIR/sp"
  printf -- '---\nname: using-superpowers\n---\nBody.\n' > "$TMPDIR/sp/SKILL.md"

  _section "[{\"name\":\"using-superpowers\",\"filePath\":\"$TMPDIR/sp/SKILL.md\"}]"
  [ "$status" -eq 0 ]
  [[ "$output" == *'subagent tool'* ]]
  [[ "$output" == *'task-list tool'* ]]
  [[ "$output" == *'TodoWrite'* ]]
}

@test "superpowers-bootstrap: the section names the skill directory relative paths resolve against" {
  # The skill links references/pi-tools.md by relative path; read from the
  # system prompt it has no directory to resolve that against unless told.
  mkdir -p "$TMPDIR/sp"
  printf -- '---\nname: using-superpowers\n---\nSee references/pi-tools.md.\n' > "$TMPDIR/sp/SKILL.md"

  _section "[{\"name\":\"using-superpowers\",\"filePath\":\"$TMPDIR/sp/SKILL.md\"}]"
  [ "$status" -eq 0 ]
  [[ "$output" == *"resolve against $TMPDIR/sp."* ]]
}

@test "superpowers-bootstrap: frontmatter closed at end of file without a newline is stripped" {
  run node --input-type=module -e "
    const { stripFrontmatter } = await import('$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap/bootstrap.ts');
    process.stdout.write(JSON.stringify(stripFrontmatter('---\nname: x\n---')));
  "
  [ "$status" -eq 0 ]
  [ "$output" = '""' ]
}

@test "superpowers-bootstrap: no skills loaded means no section" {
  # The review pipeline runs Pi with --no-skills; its parsed-JSON answers must
  # not be told to announce a skill first.
  _section '[]'
  [ "$status" -eq 0 ]
  [ "$output" = null ]
}

@test "superpowers-bootstrap: an unreadable skill file adds nothing rather than failing" {
  _section '[{"name":"using-superpowers","filePath":"/nonexistent/SKILL.md"}]'
  [ "$status" -eq 0 ]
  [ "$output" = null ]
}

@test "superpowers-bootstrap: the section name is one Pi accepts" {
  run node --input-type=module -e "
    const { SECTION_NAME } = await import('$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap/bootstrap.ts');
    process.stdout.write(String(/^[a-z][a-z0-9_-]*\$/.test(SECTION_NAME)));
  "
  [ "$output" = true ]
}

@test "superpowers-bootstrap: never edits the transcript" {
  # The whole fix: a context hook that changes messages is what broke signed
  # thinking blocks. The bootstrap belongs in the system prompt only.
  run grep -rqE 'pi\.on\("context' "$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap"
  [ "$status" -ne 0 ]
  run grep -q 'pi.on("before_agent_start"' "$REPO_ROOT/ai/pi/extensions/superpowers-bootstrap/index.ts"
  [ "$status" -eq 0 ]
}
