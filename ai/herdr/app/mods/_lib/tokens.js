/**
 * Design tokens for herdr-app mods. Pure ESM; the only import is the brand
 * module bin/local/generate-herdr-app-themes renders from @otto-nation/brand.
 * Density: compact < 560 ≤ board.
 */

import { BRAND } from "./brand.generated.js";

// xs..xl are the brand's spacing 1, 2, 3, 4 and 6. hair and xxs sit below its 4px base: herdr
// draws 1px rules and 2px track insets the brand has no step for.
export const SP = {
  hair: 1, xxs: 2,
  xs: BRAND.space["1"], sm: BRAND.space["2"], md: BRAND.space["3"], lg: BRAND.space["4"], xl: BRAND.space["6"],
};
// DESIGN.md's app typography roles, read from the brand: meta (counts, timestamps), section
// (section labels), label (buttons, segmented, tags), body (rows, default text), title (card and
// pane titles), code (branches, SHAs, paths). Apply them with _ui/ui.js type() or text().
const role = (f, mono = false) => ({ size: f.size, weight: f.weight, lh: f.lineHeight, mono });
export const TYPE = {
  meta: role(BRAND.font.meta),
  section: role(BRAND.font.section),
  label: role(BRAND.font.label),
  body: role(BRAND.font.body),
  title: role(BRAND.font.title),
  code: role(BRAND.font.code, true),
};
// tag is rounded.sm, control is rounded.md, card is rounded.lg.
export const RADIUS = { tag: BRAND.radius.sm, control: BRAND.radius.md, card: BRAND.radius.lg };
// hex alpha suffixes appended to a theme colour
export const FILL = { lane: "55", add: "18", del: "18" };
// spacing.control-height(-dense) and spacing.control-padding.
export const CONTROL = { h: BRAND.controlHeight, hDense: BRAND.controlHeightDense, px: BRAND.controlPadding };
// The tab bar's panel toggles. herdr's own tabs mod draws Files and Changes at these sizes, and
// the PRs toggle sits beside them, so it matches herdr rather than a brand control height.
export const PANEL_BUTTON = { h: 26, px: 8, gap: 5, icon: 14 };
// spacing.row-height(-dense). A sidebar row holds a body line and a meta line plus a 6px inset,
// taller than any brand row; diff is a code line.
export const ROW_H = {
  list: BRAND.rowHeight, listDense: BRAND.rowHeightDense,
  sidebar: BRAND.font.body.lineHeight + BRAND.font.meta.lineHeight + SP.xs + SP.xxs, diff: 20,
};
// compact is below the brand's board pane width. twoLine is herdr's own: the sidebar drops its
// meta line below it.
export const BREAKPOINT = { compact: BRAND.pane.board, twoLine: 200 };
export const LAYOUT = { colMin: BRAND.columnMin, colMax: 400, titleLh: BRAND.font.title.lineHeight, headerH: 52, msgMaxW: 480, bodyMaxH: 420, titleMaxH: 40, filterLabelW: 56, monoEm: 0.62, gutterDigits: 5, statusW: 14, cardTitleMaxH: 2 * BRAND.font.title.lineHeight, iconSize: 14 };
export const MONO = { charPx: Math.ceil(TYPE.code.size * LAYOUT.monoEm), gutterDigits: LAYOUT.gutterDigits, gutter: Math.ceil(TYPE.code.size * LAYOUT.monoEm) * LAYOUT.gutterDigits };
