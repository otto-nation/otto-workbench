/**
 * `spacePrs({spaces, panes, stateOf})` returns ordered rows
 * `{spaceId, spaceName, on, cwd, branch, pr, more, status, message}`: one
 * per space checkout that has a branch, joined to that repo's open PRs.
 * Targets from `spaceTargets`; spaceId/spaceName from the space that
 * produced the target; branch from the pane whose `cwd` equals the target
 * (else `space.branch`). No branch / detached / no cwd → no row. Open rows
 * (`state === 'OPEN'`, drafts included) whose `branch` matches; `pr` is the
 * lowest number with `presentation: presentationOf(pr)`; `more` is count − 1.
 * `status` is ready|loading|error from `repoPhase`; `message` from
 * `friendlyError`. Skippable errors drop the row. A ready repo with no
 * matching open PR drops the row. Loading/error rows stay (`pr` null) so
 * the view can show `…`/`×`. Order follows `spaces()`. Pure.
 */
import { presentationOf } from './pr_status.js';
import {
  friendlyError, repoPhase, skippable, spaceFolders, spacePanes, spaceTargets,
} from './scope.js';

const LOADING_PHASES = new Set(['queued', 'loading']);

function statusOfPhase(phase) {
  if (phase === 'failed') return 'error';
  if (LOADING_PHASES.has(phase)) return 'loading';
  return 'ready';
}

// The first space (in spaces() order) on the target's machine that has the target's folder.
function spaceOfTarget(spaces, panes, target) {
  return (spaces ?? []).find((s) => (s.machine ?? null) === (target.on ?? null)
    && spaceFolders(s, panes).includes(target.cwd)) ?? null;
}

// The branch of the space's pane in the target folder, else the space's own branch.
function branchOf(target, space, panes) {
  const pane = spacePanes(space, panes).find((p) => p.cwd === target.cwd);
  const branch = pane != null ? pane.branch : space.branch;
  return branch == null || branch === '' ? null : branch;
}

function openOnBranch(rows, branch) {
  return (rows ?? [])
    .filter((r) => r.state === 'OPEN' && r.branch === branch)
    .slice()
    .sort((a, b) => a.number - b.number);
}

function rowOf({ space, target, branch, pr, more, status, message }) {
  return {
    spaceId: space.id,
    spaceName: space.name,
    on: target.on,
    cwd: target.cwd,
    branch,
    pr,
    more,
    status,
    message,
  };
}

export function spacePrs({ spaces, panes, stateOf }) {
  const out = [];
  for (const target of spaceTargets(spaces, panes)) {
    const space = spaceOfTarget(spaces, panes, target);
    if (!space) continue;
    const branch = branchOf(target, space, panes);
    if (!branch) continue;
    const st = stateOf(target) ?? {};
    if (skippable(st.error)) continue;
    const status = statusOfPhase(repoPhase(st));
    const message = status === 'error' ? friendlyError(st.error, target.on) : null;
    if (status !== 'ready') {
      out.push(rowOf({ space, target, branch, pr: null, more: 0, status, message }));
      continue;
    }
    const matches = openOnBranch(st.rows, branch);
    if (!matches.length) continue;
    const pr = matches[0];
    out.push(rowOf({
      space, target, branch,
      pr: { ...pr, presentation: presentationOf(pr) },
      more: matches.length - 1,
      status,
      message,
    }));
  }
  return out;
}
