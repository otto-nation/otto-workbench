/**
 * Last-N cleaned lines of terminal/log text. Pure ESM; no imports.
 */

export function tailLines(text, max = 80) {
  if (typeof text !== 'string' || text === '') return [];
  const ansi = /\x1b\[[0-9;?]*[A-Za-z]/g;
  const lines = text.split(/\r?\n/).map((line) =>
    line.replace(ansi, '').replace(/\s+$/, '')
  );
  let end = lines.length;
  while (end > 0 && lines[end - 1] === '') end--;
  const start = end > max ? end - max : 0;
  return lines.slice(start, end);
}
