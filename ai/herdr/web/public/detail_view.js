// The PR detail: header, Review history (expandable bodies), Files and one file's diff.
// Phase 2 adds the action bar, phone bar and output panel through `slots`; Phase 4 the Batch tab.
import { badge, h, setChildren, status, tag, trustedMarkdown } from './dom.js';
import { historyRows } from '../../app/mods/_lib/history.js';
import { MAX_LINES, summarize } from '../../app/mods/_lib/diff.js';
import { prPresentation, statsText } from '../../app/mods/_lib/pr_status.js';
import { fileMarkKind } from '../../app/mods/_lib/status.js';
import { renderMarkdown } from '../lib/markdown.js';
import { prUrl, sortedFiles } from '../lib/view.js';

function header(pr, files, { onClose, slots, itemOf }) {
  const p = prPresentation(pr, { item: itemOf?.(pr) ?? null });
  const sum = files?.ok ? summarize(files.files) : null;
  const stats = sum ? statsText({ additions: sum.add, deletions: sum.del, files: sum.files }) : p.stats;
  const marks = [p.ci, p.decision, ...p.detail_issues, p.review, p.busy].filter(Boolean);
  const url = prUrl(pr);
  return h('div', { class: 'dhead' },
    h('div', { class: 'row' }, h('button', { class: 'btn ghost mback', onclick: onClose }, '‹ Back'),
      h('span', { class: 'num' }, p.ref), h('h2', {}, p.title), ...p.tags.map((name) => tag(name)),
      h('button', { class: 'icon-btn desk', title: 'Close', 'aria-label': 'Close', onclick: onClose }, '×')),
    h('div', { class: 'sub' }, h('span', {}, p.author),
      h('span', { class: 'code' }, `${p.branch}${p.base_branch ? ` → ${p.base_branch}` : ''}`),
      h('span', { class: 'meta add' }, stats.additions), h('span', { class: 'meta del' }, stats.deletions),
      h('span', { class: 'meta' }, stats.files),
      sum?.generated ? h('span', { class: 'meta' }, `excl. ${sum.generated} generated`) : null),
    marks.length ? h('div', { class: 'badges' }, marks.map(badge)) : null,
    h('div', { class: 'row desk-btns' }, slots?.headerButtons ?? null,
      url ? h('a', { class: 'btn', href: url, target: '_blank', rel: 'noopener noreferrer' }, 'View on GitHub ↗') : null));
}

function reviewBody(body) {
  if (body == null) return h('div', { class: 'inset muted' }, 'Loading…');
  if (body.ok === false) return h('div', { class: 'inset' }, status('fail', body.error?.message ?? 'Could not read the review', { role: 'body' }));
  if (body.text == null) return h('div', { class: 'inset muted' }, 'Review file not found — it may have been pruned');
  return trustedMarkdown(h('div', { class: 'inset body' }), renderMarkdown(body.text));
}

function history(pr, { openKey, bodies, onToggleRun, now }) {
  const rows = historyRows((pr.reviewRuns ?? []).map((r) => r.run), now);
  if (!rows.length) return [h('div', { class: 'none' }, 'No reviews yet')];
  return rows.map((r) => {
    const open = openKey === r.key;
    return [
      h('button', { class: 'card run', type: 'button', 'aria-expanded': open ? 'true' : 'false', onclick: () => onToggleRun(r.key) },
        h('div', { class: 'top' }, h('span', {}, r.verdict || 'review'), h('span', { class: 'meta-text' }, r.when), h('span', { class: 'code' }, r.shortSha)),
        h('div', {}, r.findingsText),
        h('div', { class: 'top' }, status(r.post.status, r.post.label), r.healthStatus ? status(r.healthStatus, r.health) : null,
          r.costText ? h('span', { class: 'meta-text' }, r.costText) : null),
        r.modelsText ? h('div', { class: 'meta-text' }, r.modelsText) : null),
      open ? h('div', { onclick: (e) => e.stopPropagation() }, reviewBody(bodies.get(r.key))) : null,
    ];
  });
}

function fileList(files, { onPick }) {
  if (files == null) return [h('div', { class: 'none' }, 'Loading…')];
  if (files.ok === false) return [status('fail', files.error?.message ?? 'Could not load changed files', { role: 'body' })];
  if (!files.files.length) return [h('div', { class: 'none' }, 'No changed files')];
  return sortedFiles(files.files).map((f) => {
    const parts = f.path.split('/');
    const name = parts.pop();
    return h('button', { class: `file${f.generated ? ' gen' : ''}`, type: 'button', onclick: () => onPick(f.path) },
      h('span', { class: `st ${fileMarkKind(f.status)}` }, f.status),
      h('span', { class: 'p' }, h('span', { class: 'dir trunc' }, parts.length ? `${parts.join('/')}/` : ''), h('span', { class: 'trunc' }, name)),
      f.generated ? h('span', { class: 'gen-note' }, 'generated') : null,
      f.add > 0 ? h('span', { class: 'add' }, `+${f.add}`) : null, f.del > 0 ? h('span', { class: 'del' }, `-${f.del}`) : null);
  });
}

const ROW_CLASS = { '@': 'h', '+': 'a', '-': 'd' };

function diffView(path, diff, { onBack }) {
  const note = diff == null ? 'Loading…' : diff.error ? diff.error : diff.rows == null ? 'No text diff for this file (binary or too large)' : null;
  return [
    h('div', { class: 'row' }, h('button', { class: 'btn', onclick: onBack }, '← Files'), h('span', { class: 'code trunc' }, path)),
    note ? h('div', { class: 'none' }, note)
      : h('div', { class: 'diff', dataset: { scroll: 'diff' } }, diff.rows.map((r) => h('div', { class: ROW_CLASS[r.kind] ?? '' },
        h('span', { class: 'g' }, r.number === undefined ? '' : String(r.number)), r.kind === '@' ? r.text : `${r.kind || ' '}${r.text}`))),
    diff?.truncated ? h('div', { class: 'meta-text' }, `Showing the first ${MAX_LINES.toLocaleString('en-US')} lines`) : null,
  ];
}

export function renderDetail(el, pr, state, handlers) {
  el.classList.toggle('open', Boolean(pr));
  document.body.classList.toggle('detail-open', Boolean(pr));
  if (!pr) { el.replaceChildren(); return; }
  const batchTab = state.slots?.batchTab ?? null;
  const tab = state.tab === 'batch' && !batchTab ? 'history' : state.tab;
  const body = tab === 'files'
    ? (state.openFile ? diffView(state.openFile, state.diff, handlers) : fileList(state.files, handlers))
    : tab === 'batch' ? batchTab
      : history(pr, { ...state, ...handlers });
  const seg = (id, label) => h('button', { class: tab === id ? 'on' : '', 'aria-selected': tab === id ? 'true' : 'false', onclick: () => handlers.onTab(id) }, label);
  setChildren(el,
    header(pr, state.files, { ...handlers, slots: state.slots }),
    state.slots?.actionBar ?? null,
    state.slots?.output ?? null,
    h('div', { class: 'tabs' }, h('div', { class: 'seg', role: 'tablist' }, seg('history', 'Review history'), seg('files', 'Files'),
      batchTab ? seg('batch', 'Batch') : null)),
    h('div', { class: 'scroll', dataset: { scroll: 'detail' } }, body),
    state.slots?.phoneBar ?? null,
  );
}
