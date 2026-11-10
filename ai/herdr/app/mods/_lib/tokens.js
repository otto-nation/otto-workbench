/**
 * Design tokens for herdr-app mods. Pure ESM; the only import is the brand
 * module bin/local/generate-herdr-app-themes renders from @otto-nation/brand.
 * Density: compact < 560 ≤ board.
 */

import { BRAND } from "./brand.generated.js";

// The brand's DESIGN.md is written in Tailwind classes; these are the app's pixel stand-ins.
// SP follows its 4px spacing base (xs..xl are spacing 1, 2, 3, 4, 6).
export const SP = { hair: 1, xxs: 2, xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
// Sizes and weights are the brand's app type roles (DESIGN.md typography.meta/label/body/title).
export const TEXT = {
  caption: BRAND.font.meta.size,
  small: BRAND.font.label.size,
  body: BRAND.font.body.size,
  title: BRAND.font.title.size,
};
export const WEIGHT = {
  regular: BRAND.font.body.weight,
  label: BRAND.font.label.weight,
  strong: BRAND.font.title.weight,
  section: BRAND.font.section.weight,
};
// The brand keeps radii as DESIGN.md constants, not tokens.css values, so they stay literal:
// tag is rounded.sm, control is rounded.md, card is rounded.lg.
export const RADIUS = { tag: 4, control: 6, card: 8 };
// hex alpha suffixes appended to a theme colour
export const FILL = { lane: "55", add: "18", del: "18" };
export const CONTROL_H = { board: BRAND.controlHeight, compact: BRAND.controlHeightDense };
// The tab bar's panel toggles (herdr's tabs mod draws Files/Changes at these sizes).
export const PANEL_BUTTON = { h: 26, px: 8, gap: 5, icon: 14 };
export const ROW_H = { sidebar: 40, sidebarTitleOnly: 28, diff: 20 };
export const BREAKPOINT = { compact: 560, twoLine: 200 };
export const LAYOUT = { colMin: BRAND.columnMin, colMax: 400, titleLh: 18, headerH: 52, msgMaxW: 480, bodyMaxH: 420, titleMaxH: 40, filterLabelW: 56, monoEm: 0.62, gutterDigits: 5, statusW: 14, cardTitleMaxH: 36 };
export const MONO = { charPx: Math.ceil(TEXT.caption * LAYOUT.monoEm), gutterDigits: LAYOUT.gutterDigits, gutter: Math.ceil(TEXT.caption * LAYOUT.monoEm) * LAYOUT.gutterDigits };
