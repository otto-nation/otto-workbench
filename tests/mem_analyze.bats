#!/usr/bin/env bats
# Tests for mem-analyze _app_name function — process name parsing for
# IntelliJ, .app bundles, Gradle/Java, macOS VMs, and plain binaries.

setup() {
  load 'test_helper'
  common_setup
  MEM_ANALYZE="$REPO_ROOT/bin/mem-analyze"

  # Extract _app_name function via sed — the script is procedural
  # and cannot be sourced without running the main body.
  eval "$(sed -n '/^_app_name()/,/^}/p' "$MEM_ANALYZE")"
}

teardown() {
  common_teardown
}

# ── IntelliJ IDEA ────────────────────────────────────────────────────────────

@test "_app_name: IntelliJ IDEA process" {
  result=$(_app_name "/Applications/IntelliJ IDEA.app/Contents/MacOS/idea" 50)
  [ "$result" = "IntelliJ IDEA" ]
}

@test "_app_name: IntelliJ with project path" {
  result=$(_app_name "/Applications/IntelliJ IDEA.app/Contents/lib/foo -Dproject=/code/myproject/build/classes" 50)
  [ "$result" = "IntelliJ IDEA (myproject)" ]
}

@test "_app_name: IntelliJ with .idea path" {
  result=$(_app_name "/Applications/IntelliJ IDEA.app/Contents/lib/foo -Dpath=/code/coolapp/.idea/workspace.xml" 50)
  [ "$result" = "IntelliJ IDEA (coolapp)" ]
}

# ── .app bundles ─────────────────────────────────────────────────────────────

@test "_app_name: .app bundle extracts app name" {
  result=$(_app_name "/Applications/Slack.app/Contents/MacOS/Slack" 50)
  [ "$result" = "Slack" ]
}

@test "_app_name: nested .app bundle" {
  result=$(_app_name "/System/Applications/Utilities/Terminal.app/Contents/MacOS/Terminal" 50)
  [ "$result" = "Terminal" ]
}

# ── Gradle/Java ──────────────────────────────────────────────────────────────

@test "_app_name: Gradle/Java process" {
  result=$(_app_name "/usr/bin/java -cp gradle-wrapper.jar org.gradle.wrapper.GradleWrapperMain" 50)
  [ "$result" = "Gradle/Java" ]
}

@test "_app_name: Gradle with project path" {
  result=$(_app_name "/usr/bin/java org.gradle.launcher -Dpath=/code/myservice/build/classes" 50)
  [ "$result" = "Gradle/Java (myservice)" ]
}

# ── macOS VM ─────────────────────────────────────────────────────────────────

@test "_app_name: Virtualization process" {
  result=$(_app_name "/System/Library/Frameworks/Virtualization.framework/Resources/vmd" 50)
  [ "$result" = "macOS VM" ]
}

# ── Plain binary fallback ────────────────────────────────────────────────────

@test "_app_name: plain binary uses basename" {
  result=$(_app_name "/usr/bin/python3" 50)
  [ "$result" = "python3" ]
}

@test "_app_name: binary with arguments includes args in basename" {
  result=$(_app_name "/usr/local/bin/node server.js" 50)
  [[ "$result" == "node server.js" ]]
}

# ── Truncation ───────────────────────────────────────────────────────────────

@test "_app_name: name exceeding max_width is truncated" {
  result=$(_app_name "/Applications/AVeryLongApplicationNameThatExceedsTheLimit.app/Contents/MacOS/run" 20)
  [ ${#result} -le 20 ]
  [[ "$result" == *"..."* ]]
}

@test "_app_name: name within max_width is not truncated" {
  result=$(_app_name "/Applications/Slack.app/Contents/MacOS/Slack" 50)
  [[ "$result" != *"..."* ]]
}

# ── CLI ──────────────────────────────────────────────────────────────────────

@test "mem-analyze --help exits 0" {
  run "$MEM_ANALYZE" --help
  [ "$status" -eq 0 ]
  [[ "$output" == *"memory analysis"* ]]
}
