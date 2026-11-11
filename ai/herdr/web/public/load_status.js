// Delayed load line, progress bar, per-repo strip, and static lane placeholders.
import { h, laneTitle, setChildren, status } from './dom.js';
import { MINE_COLUMNS, TEAM_COLUMNS } from '../../app/mods/_lib/columns.js';

export function renderLoad({ head, strip, bar }, load) {
  if (load.shown) {
    head.prepend(status('running', load.line, { wide: true }));
    bar.max = 100;
    bar.value = load.progress;
    bar.setAttribute('aria-label', load.line);
  }
  bar.hidden = !load.shown;
  setChildren(strip, load.strip.map((s) => status(s.kind, s.name, { wide: true })));
}

export function skeletonLanes(tab) {
  const spec = tab === 'mine' ? MINE_COLUMNS : TEAM_COLUMNS;
  return spec.map((c) => h('section', { class: 'col' },
    laneTitle(c.label),
    h('div', { class: 'skel' }),
    h('div', { class: 'skel short' })));
}
