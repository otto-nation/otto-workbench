/**
 * Pure helpers for a PR's changed files and their diffs. Imports nothing.
 *
 * mapFiles(json) -> [{ path, previous, status, add, del, patch }]; `status` is one letter
 * (A added, D removed, M modified, R renamed, C copied) and `patch` is null when GitHub sent none
 * (binary or too large). patchRows(patch, charPx) -> { rows, truncated, width }: rows are numbered once so a
 * frame only draws the ones on screen.
 */

export const MAX_LINES = 1500;
export const MAX_LINE_CHARS = 4000;
const DEFAULT_CHAR_PX = 7;

const LETTER = { added: 'A', removed: 'D', renamed: 'R', copied: 'C' };

export function mapFiles(json) {
  const pages = Array.isArray(json) && Array.isArray(json[0]) ? json.flat() : json;
  if (!Array.isArray(pages)) return [];
  return pages.map((f) => ({
    path: f.filename ?? '',
    previous: f.previous_filename ?? null,
    status: LETTER[f.status] ?? 'M',
    add: f.additions ?? 0,
    del: f.deletions ?? 0,
    patch: typeof f.patch === 'string' ? f.patch : null,
    generated: false,
  }));
}

export function diffRows(lines) {
  let old = 0;
  let next = 0;
  return lines.map((line) => {
    const hunk = /^@@ -(\d+)(?:,\d+)? \+(\d+)/.exec(line);
    if (hunk) {
      old = Number(hunk[1]);
      next = Number(hunk[2]);
      return { kind: '@', text: line };
    }
    const kind = line[0];
    if (kind === '-') return { kind, number: old++, text: line.slice(1) || ' ' };
    if (kind === '+') return { kind, number: next++, text: line.slice(1) || ' ' };
    if (kind === ' ') {
      old++;
      return { kind, number: next++, text: line.slice(1) || ' ' };
    }
    return { kind: '', text: line };
  });
}

// charPx is the width of one monospace glyph at the size the rows are drawn in.
export function patchRows(patch, charPx = DEFAULT_CHAR_PX) {
  if (patch == null) return { rows: [], truncated: false, width: 0 };
  const all = patch.replace(/\n$/, '').split('\n');
  const lines = all.slice(0, MAX_LINES).map((l) =>
    l.length > MAX_LINE_CHARS ? `${l.slice(0, MAX_LINE_CHARS)}… (line truncated)` : l,
  );
  const width = Math.max(300, ...lines.map((l) => l.length * charPx + 50));
  return { rows: diffRows(lines), truncated: all.length > MAX_LINES, width };
}

const CHUNK = 200;

// `git check-attr -z` argv lists, one per chunk so a big PR stays under the argument limit.
export function attrArgvs(files) {
  const out = [];
  for (let i = 0; i < files.length; i += CHUNK) {
    out.push(['check-attr', '-z', 'linguist-generated', '--', ...files.slice(i, i + CHUNK).map((f) => f.path)]);
  }
  return out;
}

// Paths `check-attr -z` reports as generated: `path\0attr\0value\0` triples; "set" and "true" count.
export function generatedPaths(stdout) {
  const parts = (stdout ?? '').split('\0');
  const out = new Set();
  for (let i = 0; i + 2 < parts.length; i += 3) {
    if (parts[i + 2] === 'set' || parts[i + 2] === 'true') out.add(parts[i]);
  }
  return out;
}

export function markGenerated(files, generated) {
  return files.map((f) => ({ ...f, generated: generated.has(f.path) }));
}

// Counts for the header: generated files are left out of every number and reported apart.
export function summarize(files) {
  const own = files.filter((f) => !f.generated);
  return {
    files: own.length,
    add: own.reduce((n, f) => n + f.add, 0),
    del: own.reduce((n, f) => n + f.del, 0),
    generated: files.length - own.length,
  };
}
