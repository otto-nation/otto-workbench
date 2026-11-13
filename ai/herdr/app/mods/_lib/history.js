/**
 * Review-history view model for the PRs board detail window.
 * Pure values; no imports. Field names match listing.py (`ReviewRun`).
 */

const POST_BY_EVENT = {
  pending: { label: 'draft on GitHub — only you can see it', status: 'warn' },
  comment: { label: 'published as comment', status: 'none' },
  approve: { label: 'approved', status: 'pass' },
  requested_changes: { label: 'changes requested', status: 'warn' },
  dismissed: { label: 'dismissed', status: 'none' },
};

const UNPOSTED = { label: 'unposted', status: 'none' };
const POSTED = { label: 'posted', status: 'none' };

const HEALTH_STATUS = { Partial: 'warn', Error: 'fail' };

const FINDING_PARTS = [
  ['must_fix', 'must-fix'],
  ['should_fix', 'should-fix'],
  ['nit', 'nit'],
  ['idiom', 'idiom'],
];

// The live run has stamp ""; identity is its reviewed_at (or started_at), never empty.
export function runKey(run) {
  const stamp = run?.stamp ?? '';
  if (stamp !== '') return stamp;
  const at = run?.reviewed_at || run?.started_at;
  return at ? `live@${at}` : 'live';
}

// Open sentinel is null. load is true when this click opens a run (cache skip is the view's).
export function toggleOpen(currentKey, key) {
  if (currentKey === key) return { open: null, load: false };
  return { open: key, load: true };
}

export function relativeTime(iso, nowMs) {
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return '';
  const delta = nowMs - t;
  if (!(delta >= 60_000)) return 'just now';
  const min = Math.floor(delta / 60_000);
  if (min < 60) return `${min}m ago`;
  const hr = Math.floor(delta / 3_600_000);
  if (hr < 24) return `${hr}h ago`;
  return `${Math.floor(delta / 86_400_000)}d ago`;
}

export function formatTokens(n) {
  if (n == null || Number.isNaN(Number(n))) return '';
  const v = Number(n);
  const abs = Math.abs(v);
  if (abs >= 1e6) return `${Number((v / 1e6).toFixed(1))}M`;
  if (abs >= 1000) return `${Number((v / 1000).toFixed(1))}k`;
  return String(v);
}

function findingsText(run) {
  if (run.structured === false) return 'details not recorded';
  const findings = run.findings ?? {};
  const parts = FINDING_PARTS.filter(([k]) => (findings[k] ?? 0) > 0)
    .map(([k, label]) => `${findings[k]} ${label}`);
  return parts.length ? parts.join(' · ') : 'no findings';
}

function costText(run) {
  if (run.structured === false) return '';
  const usd = Number(run.cost_usd);
  if (Number.isNaN(usd)) return '';
  const tokens = (run.input_tokens ?? 0) + (run.output_tokens ?? 0);
  return `$${usd.toFixed(2)} · ${formatTokens(tokens)} tok`;
}

function postOf(run) {
  if (run.post == null) return { ...UNPOSTED };
  return { ...(POST_BY_EVENT[run.post.event] ?? POSTED) };
}

function healthOf(run) {
  if (run.status === 'partial') return 'Partial';
  if (run.status === 'error') return 'Error';
  return 'ok';
}

export function historyRows(runs, nowMs) {
  const list = [...(runs ?? [])].sort((a, b) => {
    const ta = Date.parse(a.reviewed_at);
    const tb = Date.parse(b.reviewed_at);
    if (!Number.isNaN(ta) && !Number.isNaN(tb)) return tb - ta;
    return String(b.stamp ?? '').localeCompare(String(a.stamp ?? ''));
  });
  return list.map((run) => {
    const structured = run.structured !== false;
    const sha = structured && run.head_sha ? String(run.head_sha).slice(0, 7) : '';
    const health = healthOf(run);
    return {
      stamp: run.stamp ?? '',
      key: runKey(run),
      when: relativeTime(run.reviewed_at, nowMs) || run.stamp || '',
      verdict: structured ? (run.verdict ?? '') : '',
      findingsText: findingsText(run),
      costText: costText(run),
      modelsText: structured ? (run.models ?? '') : '',
      shortSha: sha,
      post: postOf(run),
      health,
      healthStatus: HEALTH_STATUS[health] ?? null,
      structured,
      reviewFile: run.review_file ?? '',
    };
  });
}
