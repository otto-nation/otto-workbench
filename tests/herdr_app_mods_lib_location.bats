#!/usr/bin/env bats
# Tests for ai/herdr/app/mods/_lib/location.js — locate, remotes, untrusted home.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/*.js lib/constants.sh

setup() {
  load 'test_helper'
  common_setup
}

teardown() {
  common_teardown
}

# $1 = JS expression using locate, stripMachine, addr, remoteSlug, hostOf,
# isUntrustedHome, UNTRUSTED_HOME_SUFFIX. Prints JSON.
_js() {
  run node --input-type=module -e "
    const {
      locate, stripMachine, addr, remoteSlug, hostOf,
      isUntrustedHome, UNTRUSTED_HOME_SUFFIX,
    } = await import('$REPO_ROOT/ai/herdr/app/mods/_lib/location.js');
    process.stdout.write(JSON.stringify(eval(process.argv[1])));
  " -- "$1"
}

@test "locate: local space" {
  _js 'locate([{active:true,folder:"/repo"}], [], {})'
  [ "$status" -eq 0 ]
  jq -e '.on == null and .cwd == "/repo" and .space.folder == "/repo"' <<<"$output"
}

@test "locate: remote space (space.machine)" {
  _js 'locate([{active:true,machine:"m",folder:"/r"}], [], {pane:"p1"})'
  [ "$status" -eq 0 ]
  jq -e '.on == "m" and .cwd == "/r"' <<<"$output"
}

@test "locate: focused.pane===null with terminal m/t1 -> on m" {
  _js 'locate([{active:true,machine:"x"}], [], {pane:null,terminal:"m/t1"})'
  [ "$status" -eq 0 ]
  jq -e '.on == "m"' <<<"$output"
}

@test "locate: mod pane without cwd falls back to space.folder" {
  _js 'locate([{active:true,folder:"/proj"}], [{pane:"mod1"}], {pane:"mod1"})'
  [ "$status" -eq 0 ]
  jq -e '.cwd == "/proj"' <<<"$output"
}

@test "locate: folder empty string -> cwd null" {
  _js 'locate([{active:true,folder:""}], [], {})'
  [ "$status" -eq 0 ]
  jq -e '.cwd == null' <<<"$output"
}

@test "locate: no active space -> space null" {
  _js 'locate([{active:false,folder:"/x"}], [], {})'
  [ "$status" -eq 0 ]
  jq -e '.space == null and .cwd == null and .on == null' <<<"$output"
}

@test "stripMachine/addr: no double prefix" {
  _js '({a:stripMachine("m/t1"),b:stripMachine("t1"),c:addr("m","t1"),d:addr("m","m/t1"),e:addr(null,"t1")})'
  [ "$status" -eq 0 ]
  jq -e '.a=="t1" and .b=="t1" and .c=="m/t1" and .d=="m/t1" and .e=="t1"' <<<"$output"
}

@test "remoteSlug: https, .git, git@, ssh://, trailing slash/newline, garbage" {
  _js '({h:remoteSlug("https://github.com/o/r"),g:remoteSlug("https://github.com/o/r.git"),s:remoteSlug("git@github.com:o/r.git"),u:remoteSlug("ssh://git@github.com/o/r.git"),t:remoteSlug("https://github.com/o/r/\n"),z:remoteSlug("not a url")})'
  [ "$status" -eq 0 ]
  jq -e '.h=="o/r" and .g=="o/r" and .s=="o/r" and .u=="o/r" and .t=="o/r" and .z==null' <<<"$output"
}

@test "hostOf" {
  _js '({h:hostOf("https://github.com/o/r"),s:hostOf("git@github.com:o/r.git"),u:hostOf("ssh://git@example.com/o/r")})'
  [ "$status" -eq 0 ]
  jq -e '.h=="github.com" and .s=="github.com" and .u=="example.com"' <<<"$output"
}

@test "isUntrustedHome boundaries" {
  _js '({a:isUntrustedHome("/x/.config/herdr-app-untrusted"),b:isUntrustedHome("/x/.config/herdr-app-untrusted2"),c:isUntrustedHome("/x/.config/herdr-app"),d:isUntrustedHome("/x/.config/herdr-app-untrusted/")})'
  [ "$status" -eq 0 ]
  jq -e '.a==true and .b==false and .c==false and .d==true' <<<"$output"
}

@test "UNTRUSTED_HOME_SUFFIX matches lib/constants.sh" {
  suffix=$(sed -n 's/^HERDR_UNTRUSTED_APP_DIR="\$HOME\(.*\)"$/\1/p' "$REPO_ROOT/lib/constants.sh")
  _js 'UNTRUSTED_HOME_SUFFIX'
  [ "$status" -eq 0 ]
  [ "$(jq -r . <<<"$output")" = "$suffix" ]
  [ "$suffix" = "/.config/herdr-app-untrusted" ]
}
