/**
 * Design tokens for herdr-app mods. Pure ESM; the only import is the brand
 * module bin/local/generate-herdr-app-themes renders from @otto-nation/brand.
 * Density: compact < 560 ≤ board.
 */

import { BRAND } from "./brand.generated.js";

// The brand's DESIGN.md is written in Tailwind classes; these are the app's pixel stand-ins.
// SP follows its 4px spacing base (xs..xl are spacing 1, 2, 3, 4, 6).
export const SP = { hair: 1, xxs: 2, xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
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
// The brand keeps radii as DESIGN.md constants, not tokens.css values, so they stay literal:
// tag is rounded.sm, control is rounded.md, card is rounded.lg.
export const RADIUS = { tag: 4, control: 6, card: 8 };
// hex alpha suffixes appended to a theme colour
export const FILL = { lane: "55", add: "18", del: "18" };
// spacing.control-height(-dense) and spacing.control-padding.
export const CONTROL = { h: BRAND.controlHeight, hDense: BRAND.controlHeightDense, px: BRAND.controlPadding };
// The tab bar's panel toggles (herdr's tabs mod draws Files/Changes at these sizes).
export const PANEL_BUTTON = { h: 26, px: 8, gap: 5, icon: 14 };
// spacing.row-height(-dense); sidebar rows hold two lines; diff is a code line.
export const ROW_H = { list: BRAND.rowHeight, listDense: BRAND.rowHeightDense, sidebar: 40, diff: 20 };
export const BREAKPOINT = { compact: 560, twoLine: 200 };
export const LAYOUT = { colMin: BRAND.columnMin, colMax: 400, titleLh: BRAND.font.title.lineHeight, headerH: 52, msgMaxW: 480, bodyMaxH: 420, titleMaxH: 40, filterLabelW: 56, monoEm: 0.62, gutterDigits: 5, statusW: 14, cardTitleMaxH: 2 * BRAND.font.title.lineHeight, iconSize: 14 };
export const MONO = { charPx: Math.ceil(TYPE.code.size * LAYOUT.monoEm), gutterDigits: LAYOUT.gutterDigits, gutter: Math.ceil(TYPE.code.size * LAYOUT.monoEm) * LAYOUT.gutterDigits };
