/**
 * A PR's GitHub page and the values its header lets you copy: one owner for the mod's detail and
 * the web's, so the two cannot disagree on what a click copies or what it says afterwards. Pure.
 */

// How long a copied value reads "Copied" before the label returns.
export const COPIED_MS = 1500;
export const COPIED_TEXT = 'Copied';

// Only a credential-free https URL is ever put in an href or handed to `open`.
function httpsUrl(raw) {
  if (typeof raw !== 'string' || /\s/.test(raw)) return '';
  try {
    const u = new URL(raw);
    if (u.protocol !== 'https:' || u.username || u.password) return '';
    return raw;
  } catch {
    return '';
  }
}

function hostName(host) {
  const raw = String(host || 'github.com');
  try {
    const u = new URL(`https://${raw}`);
    if (u.username || u.password || u.port || u.pathname !== '/' || u.search || u.hash) return 'github.com';
    if (u.hostname !== raw.toLowerCase()) return 'github.com';
    return u.hostname;
  } catch {
    return 'github.com';
  }
}

// The PR's page from a board record ({url, repoSlug, host, number}); '' when it has neither.
export function prUrl(pr) {
  const ok = httpsUrl(pr.url);
  if (ok) return ok;
  if (!pr.repoSlug || !pr.number) return '';
  return `https://${hostName(pr.host)}/${pr.repoSlug}/pull/${pr.number}`;
}

// What the header offers to copy, in order: `key` names it, `value` is what lands on the clipboard
// (the full form, not the shortened text the header shows). Absent values are left out.
export function copyFields(pr) {
  const sha = String(pr.headSha ?? '');
  const fields = [
    { key: 'ref', label: 'PR', value: pr.repoSlug && pr.number ? `${pr.repoSlug}#${pr.number}` : '' },
    { key: 'branch', label: 'Branch', value: String(pr.branch ?? '') },
    { key: 'sha', label: 'Commit', value: sha },
    { key: 'link', label: 'Link', value: prUrl(pr) },
  ];
  return fields.filter((f) => f.value !== '');
}

export function shortSha(sha) {
  return String(sha ?? '').slice(0, 7);
}
