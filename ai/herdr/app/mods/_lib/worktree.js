/**
 * Pure helpers for Open for Review: check a PR out with the workbench's `wt` and land in it.
 * Imports nothing.
 *
 * `wt switch pr:<n> --no-cd --format json` prints a JSON object on stdout,
 * often pretty-printed after a human status line:
 *   { action: "created" | "existing" | "already_at", branch, path, ... }
 */

// Slice from *start* through the matching `}`, so a later token on stdout is ignored.
function _firstObject(s, start) {
  let depth = 0, inStr = false, esc = false;
  for (let i = start; i < s.length; i++) {
    const c = s[i];
    if (inStr) {
      if (esc) esc = false;
      else if (c === '\\') esc = true;
      else if (c === '"') inStr = false;
      continue;
    }
    if (c === '"') inStr = true;
    else if (c === '{') depth++;
    else if (c === '}') {
      depth--;
      if (depth === 0) return s.slice(start, i + 1);
    }
  }
  return s.slice(start);
}

export function switchArgv(number) {
  return ['switch', `pr:${number}`, '--no-cd', '--format', 'json'];
}

export function parseSwitch(stdout) {
  const start = stdout.indexOf('{');
  if (start < 0) {
    return { ok: false, message: 'wt printed no JSON' };
  }
  let doc;
  try {
    doc = JSON.parse(_firstObject(stdout, start));
  } catch {
    return { ok: false, message: 'wt printed no JSON' };
  }
  if (typeof doc?.path !== 'string' || doc.path === '') {
    return { ok: false, message: 'wt did not report a worktree path' };
  }
  return { ok: true, path: doc.path, created: doc.action === 'created' };
}

// Only a checkout on this machine can become a space here; the reason names why not.
export function openForReviewState({ cwd, on }) {
  if (on) return { enabled: false, reason: 'Open for Review works on this machine only' };
  if (!cwd) return { enabled: false, reason: 'No local checkout of this repository' };
  return { enabled: true, reason: '' };
}

// The space already sitting on the worktree, else null.
export function spaceFor(spaces, path) {
  const hit = (spaces ?? []).find((s) => s.machine == null && s.folder === path);
  return hit ? hit.id : null;
}
