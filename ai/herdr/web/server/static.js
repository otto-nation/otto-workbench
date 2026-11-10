/**
 * The files the page may load, fixed at start. URL paths mirror the tree under ai/herdr/, so the
 * relative imports the server uses (`../../app/mods/_lib/x.js`) resolve the same way in the browser.
 * Templates (*.html) are rendered by pages.js, never served raw.
 */
import { readdir } from 'node:fs/promises';

export const TYPES = {
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.json': 'application/json; charset=utf-8',
};

const SERVED = [['app/mods/_lib/', false], ['web/lib/', false], ['web/public/', true]];

async function listFiles(dirUrl, recursive, prefix = '') {
  const out = [];
  for (const entry of await readdir(dirUrl, { withFileTypes: true })) {
    if (entry.name.startsWith('.')) continue;
    if (entry.isDirectory()) {
      if (recursive) out.push(...await listFiles(new URL(`${entry.name}/`, dirUrl), true, `${prefix}${entry.name}/`));
      continue;
    }
    const ext = entry.name.slice(entry.name.lastIndexOf('.'));
    if (TYPES[ext]) out.push(`${prefix}${entry.name}`);
  }
  return out;
}

export async function loadStatics(herdrRoot) {
  const map = new Map();
  for (const [dir, recursive] of SERVED) {
    for (const rel of await listFiles(new URL(dir, herdrRoot), recursive)) {
      map.set(`/${dir}${rel}`, new URL(dir + rel, herdrRoot));
    }
  }
  return map;
}
