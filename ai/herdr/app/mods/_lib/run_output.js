/**
 * View model for a herdr action terminal's output panel. Pure ESM.
 * Status kinds are `_lib/status.js`'s (running / pass / fail). Elapsed uses
 * `batch.js` `durationText` so batch step cards and this panel share one formatter.
 */

import { durationText } from './batch.js';
import { tailLines } from './output.js';

const REVIEW_ACTIONS = new Set(['review', 'rerun', 'recover']);

const TITLE = Object.freeze({
  review: 'Review',
  rerun: 'Re-run',
  recover: 'Recover',
  'post-draft': 'Post draft',
  'post-comment': 'Post comment',
  'post-approve': 'Approve',
  'post-request-changes': 'Request changes',
  'publish-comment': 'Publish draft',
  'publish-approve': 'Approve',
  'publish-request-changes': 'Request changes',
  dismiss: 'Dismiss',
  'batch-fix': 'Fix',
  watch: 'Watch',
});

// Spinner frames, bar glyphs, and dots-only lines — not a status sentence.
const PROGRESS_ONLY = /^[\u2800-\u28FF◐◓◑◒⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏.|•·…━─=#*░▒▓█▌▐▀▄/\s-]+$/;

function isProgressLine(line) {
  const s = String(line).trim();
  if (s === '') return true;
  if (/^\d{1,3}%$/.test(s)) return true;
  return PROGRESS_ONLY.test(s);
}

function lastMeaningful(lines) {
  for (let i = lines.length - 1; i >= 0; i--) {
    if (!isProgressLine(lines[i])) return lines[i];
  }
  return '';
}

function iso(ms) {
  if (!Number.isFinite(ms)) return '';
  return new Date(ms).toISOString();
}

function statusOf(terminal) {
  const state = terminal?.state;
  if (terminal == null || state == null || state === '' || state === 'live') return 'running';
  return terminal.exitCode === 0 ? 'pass' : 'fail';
}

function landedRun(action, runs, startedMs) {
  if (!REVIEW_ACTIONS.has(action) || !Number.isFinite(startedMs)) return null;
  let best = null;
  let bestAt = -Infinity;
  for (const run of runs ?? []) {
    const at = Date.parse(run?.reviewed_at);
    if (Number.isNaN(at) || at < startedMs) continue;
    if (at >= bestAt) {
      bestAt = at;
      best = run;
    }
  }
  return best;
}

export function summaryLine(text, max = 80) {
  return lastMeaningful(tailLines(text ?? '', max));
}

export function outputModel(input = {}) {
  const { action, terminal, startedMs, endedMs, nowMs, text, runs } = input;
  const lines = tailLines(typeof text === 'string' ? text : '', 80);
  const endIso = Number.isFinite(endedMs) ? iso(endedMs) : '';
  return {
    status: statusOf(terminal),
    title: TITLE[action] ?? 'Action',
    elapsed: durationText(iso(startedMs), endIso, nowMs),
    summary: lastMeaningful(lines),
    lines,
    landed: landedRun(action, runs, startedMs),
  };
}
