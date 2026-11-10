// The PR detail: header, Review history (expandable bodies), Files and one file's diff.
// Phase 2 adds the action bar, phone bar and output panel through `slots`; Phase 4 the Batch tab.
import { h, setChildren, status, trustedMarkdown } from './dom.js';
import { ciMark } from './board_view.js';
import { historyRows } from '../../app/mods/_lib/history.js';
import { MAX_LINES, summarize } from '../../app/mods/_lib/diff.js';
import { renderMarkdown } from '../lib/markdown.js';
import { decisionText, fileMark, healthKind, postKind, prUrl, sortedFiles } from '../lib/view.js';

function header(pr, files, { onClose, slots }) {
  const sum = files?.ok ? summarize(files.files) : null;
  const url = prUrl(pr);
  return h('div', { class: 'dhead' },
    h('div', { class: 'row' }, h('button', { class: 'btn ghost mback', onclick: onClose }, '‹ Back'),
      h('span', { class: 'num' }, `#${pr.number}`), h('h2', {}, pr.title ?? ''),
      h('button', { class: 'icon-btn desk', title: 'Close', 'aria-label': 'Close', onclick: onClose }, '×')),
    h('div', { class: 'sub' }, h('span', {}, `${(pr.repoSlug ?? '').split('/').pop()} · ${pr.author ?? ''}`),
      h('span', { class: 'code' }, `${pr.branch ?? ''}${pr.baseBranch ? ` → ${pr.baseBranch}` : ''}`),
      h('span', { class: 'meta add' }, `+${sum ? sum.add : pr.additions ?? 0}`), h('span', { class: 'meta del' }, `-${sum ? sum.del : pr.deletions ?? 0}`),
      h('span', { class: 'meta' }, `${sum ? sum.files : pr.changedFiles ?? 0} files`),
      sum?.generated ? h('span', { class: 'meta' }, `excl. ${sum.generated} generated`) : null, ciMark(pr.ciStatus, 'body'),
      h('span', { class: 'meta' }, decisionText(pr.reviewDecision))),
    h('div', { class: 'row desk-btns' }, slots?.headerButtons ?? null,
      url ? h('a', { class: 'btn', href: url, target: '_blank', rel: 'noopener noreferrer' }, 'View on GitHub ↗') : null));
}

function reviewBody(body) {
  if (body == null) return h('div', { class: 'inset muted' }, 'Loading…');
  if (body.ok === false) return h('div', { class: 'inset' }, status('fail', body.error?.message ?? 'Could not read the review', { role: 'body' }));
  if (body.text == null) return h('div', { class: 'inset muted' }, 'Review file not found — it may have been pruned');
  return trustedMarkdown(h('div', { class: 'inset body' }), renderMarkdown(body.text));
}

function history(pr, { openStamp, bodies, onToggleRun, now }) {
  const rows = historyRows((pr.reviewRuns ?? []).map((r) => r.run), now);
  if (!rows.length) return [h('div', { class: 'none' }, 'No reviews yet')];
  return rows.map((r) => {
    const health = healthKind(r.health);
    const open = openStamp === r.stamp;
    return [
      h('button', { class: 'card run', type: 'button', 'aria-expanded': open ? 'true' : 'false', onclick: () => onToggleRun(r.stamp) },
        h('div', { class: 'top' }, h('span', {}, r.verdict || 'review'), h('span', { class: 'meta-text' }, r.when), h('span', { class: 'code' }, r.shortSha)),
        h('div', {}, r.findingsText),
        h('div', { class: 'top' }, status(postKind(r.post.tone), r.post.label), health ? status(health, r.health) : null,
          r.costText ? h('span', { class: 'meta-text' }, r.costText) : null)),
      open ? h('div', { onclick: (e) => e.stopPropagation() }, reviewBody(bodies.get(r.stamp))) : null,
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
      h('span', { class: `st ${fileMark(f.status)}` }, f.status),
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
