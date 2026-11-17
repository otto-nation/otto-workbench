#!/usr/bin/env bats
# The fix buttons a PR's detail offers for its own problems, and that an own PR is not offered Approve.
bats_require_minimum_version 1.5.0
# select-on: ai/herdr/app/mods/_lib/pr_fixes.js ai/herdr/app/mods/_lib/actions.js ai/herdr/app/mods/prs/*.js

setup() {
  load 'test_helper'
  common_setup
  export NODE_COMPILE_CACHE="$BATS_FILE_TMPDIR/node-compile-cache"
  LIB="$REPO_ROOT/ai/herdr/app/mods/_lib"
}

teardown() {
  common_teardown
}

@test "fixesFor: one button per present problem, comments and changes-requested share one, none off Mine or once landed" {
  run node --input-type=module -e "
    const m = await import('$LIB/pr_fixes.js');
    const row = (kinds, extra = {}) => ({ isMine: true, state: 'OPEN', issues: kinds.map((kind) => ({ kind })), ...extra });
    const ids = (pr) => m.fixesFor(pr).map((f) => f.id + ':' + f.steps.join('+') + (f.ai ? ':ai' : ''));
    process.stdout.write(JSON.stringify([
      ids(row(['conflicts', 'ci-failing', 'has-comments', 'changes-requested'])),
      ids(row(['ci-running', 'ci-unknown', 'ci-pending'])),
      ids(row(['conflicts'], { isMine: false })),
      ids(row(['conflicts'], { state: 'MERGED' })),
      ids({ isMine: true }),
    ]));"
  [ "$status" -eq 0 ]
  [ "$output" = '[["fix-rebase:rebase:ai","fix-ci:ci:ai","fix-comments:comments:ai"],[],[],[],[]]' ]
}

@test "actionsFor leaves Approve and Request changes off an own PR, and keeps them on someone else's" {
  run node --input-type=module -e "
    const m = await import('$LIB/actions.js');
    const run = { status: 'ok', head_sha: 'abcdef1234', post: null };
    const ctx = { repo: 'o/r', number: 1, repoDir: '/x', run, features: ['review-publish'] };
    const ids = (own) => m.actionsFor({ ...ctx, own }).map((a) => a.id).filter((id) => /approve|request/.test(id));
    process.stdout.write(JSON.stringify([ids(false), ids(true)]));"
  [ "$status" -eq 0 ]
  [ "$output" = '[["post-approve","post-request-changes","publish-approve","publish-request-changes"],[]]' ]
}
