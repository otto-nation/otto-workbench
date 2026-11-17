/**
 * `spaceWorktreePrs({spaces, panes, terminals, agents, stateOf})` returns
 * ordered groups `{spaceId, spaceName, on, repo, status, message, rows}`:
 * one per distinct repo among `spaceTargets`, named after the first space
 * on that repo. Rows are the repo's OPEN PRs with a non-null `worktree`,
 * grouped by branch (lowest number shown, `more` is the rest), sorted by
 * number. Each row is `{branch, worktree, pr:{…, presentation:
 * presentationOf(pr)}, more, owner}`. `owner` is `{terminal, pane, name}`
 * from `agentPaneFor` (agent tiers only) or null; `name` is the agent's
 * `tab_name`, prefixed `<space_name> / ` when that agent's space is not
 * the group's, else `pane.app`. `status` is ready|loading|error|outdated;
 * `outdated` when `features` is an array without `worktrees`. Skippable
 * errors drop the group. A ready group with no rows is dropped.
 * Loading/error/outdated groups stay so the view can show `…`/`×`/the
 * update text. Order follows `spaces()`. Pure.
 */
import { presentationOf } from './pr_status.js';
import { agentPaneFor } from './run_session.js';
import {
  friendlyError, repoId, repoPhase, skippable, spaceFolders, spaceTargets,
} from './scope.js';

export const WORKTREES_OUTDATED_TEXT =
  'Update pr on this machine to list worktrees (otto-workbench sync)';

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

function openWorktreeBranches(rows) {
  const byBranch = new Map();
  for (const r of rows ?? []) {
    if (r.state !== 'OPEN' || r.worktree == null) continue;
    const list = byBranch.get(r.branch);
    if (list) list.push(r);
    else byBranch.set(r.branch, [r]);
  }
  const groups = [];
  for (const [branch, list] of byBranch) {
    list.sort((a, b) => a.number - b.number);
    groups.push({
      branch,
      worktree: list[0].worktree,
      pr: list[0],
      more: list.length - 1,
    });
  }
  groups.sort((a, b) => a.pr.number - b.pr.number);
  return groups;
}

function ownerOf(pane, { agents, groupSpace }) {
  if (!pane) return null;
  const agent = (agents ?? []).find((a) => a.address === pane.terminal) ?? null;
  let name = (typeof agent?.tab_name === 'string' && agent.tab_name !== '')
    ? agent.tab_name
    : (pane.app ?? null);
  if (name != null && agent != null && agent.space !== groupSpace.id && agent.space_name) {
    name = `${agent.space_name} / ${name}`;
  }
  return { terminal: pane.terminal, pane: pane.pane, name };
}

function groupOf({ space, target, repo, status, message, rows }) {
  return {
    spaceId: space.id,
    spaceName: space.name,
    on: target.on,
    repo,
    status,
    message,
    rows,
  };
}

export function spaceWorktreePrs({ spaces, panes, terminals, agents, stateOf }) {
  const seen = new Set();
  const out = [];
  for (const target of spaceTargets(spaces, panes)) {
    const space = spaceOfTarget(spaces, panes, target);
    if (!space) continue;
    const st = stateOf(target) ?? {};
    if (skippable(st.error)) continue;
    const id = st.repo ? repoId(st.repo) : null;
    if (id != null) {
      if (seen.has(id)) continue;
      seen.add(id);
    }
    const outdated = Array.isArray(st.features) && !st.features.includes('worktrees');
    const status = outdated ? 'outdated' : statusOfPhase(repoPhase(st));
    const message = status === 'outdated'
      ? WORKTREES_OUTDATED_TEXT
      : status === 'error' ? friendlyError(st.error, target.on) : null;
    if (status !== 'ready') {
      out.push(groupOf({ space, target, repo: st.repo ?? null, status, message, rows: [] }));
      continue;
    }
    const rows = openWorktreeBranches(st.rows).map((row) => ({
      branch: row.branch,
      worktree: row.worktree,
      pr: { ...row.pr, presentation: presentationOf(row.pr) },
      more: row.more,
      owner: ownerOf(
        agentPaneFor(panes, { on: target.on, cwd: row.worktree, terminals }),
        { agents, groupSpace: space },
      ),
    }));
    if (!rows.length) continue;
    out.push(groupOf({ space, target, repo: st.repo ?? null, status, message, rows }));
  }
  return out;
}
