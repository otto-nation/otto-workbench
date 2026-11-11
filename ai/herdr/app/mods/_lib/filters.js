/**
 * Include/exclude filter actions, matching, and the compact filter-bar model.
 *
 * Renderers (mod and web) only draw; this module owns the state transitions.
 */

import {
  DEFAULT_FILTERS,
  filterFacets,
  filterLabels,
  filterOn,
  filterRepos,
} from './columns.js';

export const SEARCH_MIN = 8;

const FLAG_KEYS = Object.keys(DEFAULT_FILTERS);

const LIST_KEYS = Object.freeze([
  'repos',
  'labels',
  'authors',
  'excludeRepos',
  'excludeLabels',
  'excludeAuthors',
]);

const INCLUDE_OF = Object.freeze({
  repo: 'repos',
  label: 'labels',
  author: 'authors',
});

const EXCLUDE_OF = Object.freeze({
  'not-repo': 'excludeRepos',
  'not-label': 'excludeLabels',
  'not-author': 'excludeAuthors',
});

const OTHER_OF = Object.freeze({
  repo: 'excludeRepos',
  label: 'excludeLabels',
  author: 'excludeAuthors',
  'not-repo': 'repos',
  'not-label': 'labels',
  'not-author': 'authors',
});

const FACET_META = Object.freeze({
  repos: { key: 'repos', label: 'Repo', prefix: 'repo' },
  labels: { key: 'labels', label: 'Label', prefix: 'label' },
  authors: { key: 'authors', label: 'Author', prefix: 'author' },
});

function fold(s) {
  return String(s ?? '').toLowerCase();
}

function normalizeList(raw) {
  if (!Array.isArray(raw)) return [];
  const out = [];
  const seen = new Set();
  for (const item of raw) {
    const v = fold(item);
    if (seen.has(v)) continue;
    seen.add(v);
    out.push(v);
  }
  return out;
}

function minus(include, exclude) {
  const drop = new Set(exclude);
  return include.filter((v) => !drop.has(v));
}

export function isBot(login) {
  return /\[bot\]$/i.test(String(login ?? ''));
}

export function normalizeFilters(raw) {
  const src = raw ?? {};
  const out = {};
  for (const key of FLAG_KEYS) {
    out[key] = typeof src[key] === 'boolean' ? src[key] : DEFAULT_FILTERS[key];
  }
  const lists = {};
  for (const key of LIST_KEYS) {
    lists[key] = normalizeList(src[key]);
  }
  lists.repos = minus(lists.repos, lists.excludeRepos);
  lists.labels = minus(lists.labels, lists.excludeLabels);
  lists.authors = minus(lists.authors, lists.excludeAuthors);
  return { ...out, ...lists };
}

function toggleList(list, value) {
  return list.includes(value) ? list.filter((v) => v !== value) : [...list, value];
}

export function filterAction(filters, action) {
  const name = String(action ?? '');
  if (!name) return filters;
  if (FLAG_KEYS.includes(name)) {
    const next = normalizeFilters(filters);
    next[name] = !filterOn(filters, name);
    return next;
  }
  const colon = name.indexOf(':');
  if (colon < 0) return filters;
  const kind = name.slice(0, colon);
  const value = fold(name.slice(colon + 1));
  if (!value) return filters;
  if (kind === 'clear') {
    if (value !== 'team' && value !== 'mine' && value !== 'batch') return filters;
    const next = normalizeFilters(filters);
    next.repos = [];
    next.labels = [];
    next.excludeRepos = [];
    next.excludeLabels = [];
    if (value === 'team') {
      next.authors = [];
      next.excludeAuthors = [];
      next.hideBots = false;
    }
    return next;
  }
  const listKey = INCLUDE_OF[kind] ?? EXCLUDE_OF[kind];
  if (!listKey) return filters;
  const next = normalizeFilters(filters);
  next[listKey] = toggleList(next[listKey], value);
  next[OTHER_OF[kind]] = next[OTHER_OF[kind]].filter((v) => v !== value);
  return next;
}

function dropExcluded(prs, exclude, field) {
  const drop = new Set(exclude);
  if (drop.size === 0) return prs;
  if (field === 'labels') {
    return prs.filter((p) => !(p.labels ?? []).some((n) => drop.has(fold(n))));
  }
  return prs.filter((p) => !drop.has(fold(p[field])));
}

function authorNames(prs, me) {
  const who = fold(me);
  const seen = new Set();
  for (const pr of prs ?? []) {
    const a = fold(pr?.author);
    if (!a || a === who) continue;
    seen.add(a);
  }
  return [...seen].sort((a, b) => a.localeCompare(b));
}

export function facetView(base, filters, {
  tab = 'team',
  me = '',
  loadedRepos,
  knownLabels,
  authorsFrom,
} = {}) {
  const f = normalizeFilters(filters);
  const authors = authorNames(authorsFrom ?? base, me);
  let list = filterRepos(base ?? [], f.repos, loadedRepos);
  list = dropExcluded(list, f.excludeRepos, 'repoSlug');
  const offered = filterFacets(list).labels;
  list = filterLabels(list, f.labels, knownLabels ?? offered);
  list = dropExcluded(list, f.excludeLabels, 'labels');
  if (tab === 'team') {
    const who = fold(me);
    const known = new Set(authors);
    const includeAuthors = f.authors.filter((a) => a !== who && known.has(a));
    if (includeAuthors.length > 0) {
      const want = new Set(includeAuthors);
      list = list.filter((p) => want.has(fold(p.author)));
    }
    list = dropExcluded(list, f.excludeAuthors, 'author');
    if (filterOn(f, 'hideBots')) {
      list = list.filter((p) => !isBot(p.author));
    }
  }
  return { list, options: { labels: offered, authors } };
}

function repoLabel(slug) {
  const parts = String(slug ?? '').split('/');
  return parts[parts.length - 1] || String(slug ?? '');
}

function facetOptions(prefix, offered, includes, excludes, labelOf) {
  const includeSet = new Set(includes);
  const excludeSet = new Set(excludes);
  const rows = [];
  const seen = new Set();
  for (const raw of offered ?? []) {
    const value = fold(raw);
    if (!value || seen.has(value)) continue;
    seen.add(value);
    const state = excludeSet.has(value) ? 'exclude' : includeSet.has(value) ? 'include' : null;
    rows.push({
      value,
      label: labelOf(raw),
      state,
      include: `${prefix}:${value}`,
      exclude: `not-${prefix}:${value}`,
    });
  }
  for (const value of excludes) {
    if (seen.has(value)) continue;
    seen.add(value);
    rows.push({
      value,
      label: labelOf(value),
      state: 'exclude',
      include: `${prefix}:${value}`,
      exclude: `not-${prefix}:${value}`,
    });
  }
  rows.sort((a, b) => Number(Boolean(b.state)) - Number(Boolean(a.state)));
  return rows;
}

function showRows(f, tab) {
  const draftKey = tab === 'team' ? 'drafts' : 'mineDrafts';
  const rows = [
    { key: draftKey, label: 'Drafts', on: filterOn(f, draftKey), action: draftKey },
  ];
  if (tab !== 'batch') {
    rows.push(
      { key: 'landed', label: 'Landed', on: filterOn(f, 'landed'), action: 'landed' },
      { key: 'empty', label: 'Empty lanes', on: filterOn(f, 'empty'), action: 'empty' },
    );
  }
  return rows;
}

function valueTags(options, excludePrefix) {
  const tags = [];
  for (const o of options) {
    if (o.state === 'include') tags.push({ text: o.label, action: o.include, excluded: false });
  }
  for (const o of options) {
    if (o.state === 'exclude') {
      tags.push({ text: `${excludePrefix}${o.label}`, action: o.exclude, excluded: true });
    }
  }
  return tags;
}

export function barModel(filters, { tab = 'team', options = {} } = {}) {
  const f = normalizeFilters(filters);
  const reposOffered = options.repos ?? [];
  const labelsOffered = options.labels ?? [];
  const authorsOffered = options.authors ?? [];
  const rows = showRows(f, tab);
  const bots = tab === 'team'
    ? {
      label: 'Hide bots',
      hint: 'login ends in [bot]',
      on: filterOn(f, 'hideBots'),
      action: 'hideBots',
    }
    : null;
  const facets = [{
    key: 'show',
    label: 'Show',
    active: rows.some((r) => r.on) || Boolean(bots?.on),
    rows,
    bots,
  }];
  const repoOpts = facetOptions('repo', reposOffered, f.repos, f.excludeRepos, repoLabel);
  const labelOpts = facetOptions('label', labelsOffered, f.labels, f.excludeLabels, (v) => String(v));
  const authorOpts = facetOptions('author', authorsOffered, f.authors, f.excludeAuthors, (v) => String(v));
  if (reposOffered.length > 1) {
    facets.push({
      key: 'repos',
      label: 'Repo',
      active: repoOpts.some((o) => o.state),
      search: repoOpts.length > SEARCH_MIN,
      options: repoOpts,
    });
  }
  facets.push({
    key: 'labels',
    label: 'Label',
    active: labelOpts.some((o) => o.state),
    search: labelOpts.length > SEARCH_MIN,
    options: labelOpts,
  });
  if (tab === 'team') {
    facets.push({
      key: 'authors',
      label: 'Author',
      active: authorOpts.some((o) => o.state),
      search: authorOpts.length > SEARCH_MIN,
      options: authorOpts,
    });
  }
  const tags = [
    ...valueTags(repoOpts, 'not '),
    ...valueTags(labelOpts, 'not '),
  ];
  if (tab === 'team') {
    tags.push(...valueTags(authorOpts, 'not '));
    if (bots?.on) tags.push({ text: 'Bots hidden', action: 'hideBots', excluded: false });
  }
  const clear = tags.length > 0 ? `clear:${tab}` : null;
  const count = tags.length + rows.filter((r) => r.on).length;
  return { facets, tags, clear, count };
}

export function searchOptions(rows, query) {
  const q = fold(query).trim();
  if (!q) return rows ?? [];
  return (rows ?? []).filter((r) => (
    r.state || fold(r.label).includes(q) || fold(r.value).includes(q)
  ));
}
