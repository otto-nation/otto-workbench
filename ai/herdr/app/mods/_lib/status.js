/**
 * DESIGN.md's status vocabulary: each kind's glyph and the theme field that colours it. One owner for
 * the mod (_ui/ui.js statusText) and the web (public/dom.js status, lib/css.js --status-*). Keys and
 * their order are board.model.StatusKind's (tests/herdr_app_mods_lib_pr_status.bats holds them equal).
 * Fail is × (U+00D7): no brand font draws U+2717. The palette puts danger on ANSI 1 and warn on ANSI 3.
 */

export const STATUS = Object.freeze({
  pass: Object.freeze({ glyph: '✓', field: 'done' }),
  fail: Object.freeze({ glyph: '×', field: 'ansi1' }),
  running: Object.freeze({ glyph: '…', field: 'working' }),
  warn: Object.freeze({ glyph: '!', field: 'ansi3' }),
  none: Object.freeze({ glyph: '·', field: 'muted' }),
});

export const STATUS_KINDS = Object.freeze(Object.keys(STATUS));

export const GLYPH = Object.freeze(Object.fromEntries(STATUS_KINDS.map((k) => [k, STATUS[k].glyph])));

// An unknown kind reads as none.
export function statusOf(kind) {
  return STATUS[kind] ?? STATUS.none;
}

// Diff counts and file marks by theme field: added in done, deleted in danger, anything else muted.
export const DIFF = Object.freeze({ add: 'done', del: 'ansi1', mod: 'muted' });

// A changed file's git status letter as a DIFF key: A is add, D is del, the rest mod.
export function fileMarkKind(status) {
  if (status === 'A') return 'add';
  if (status === 'D') return 'del';
  return 'mod';
}
