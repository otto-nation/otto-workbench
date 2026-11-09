/**
 * Design tokens for herdr-app mods. Pure ESM; no imports.
 * Density: compact < 560 ≤ board.
 */

export const SP = { hair: 1, xxs: 2, xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
export const TEXT = { caption: 11, small: 12, body: 13, title: 14 };
export const RADIUS = { control: 6, card: 8 };
// hex alpha suffixes appended to a theme colour
export const FILL = { lane: "55", add: "18", del: "18" };
export const CONTROL_H = { board: 28, compact: 24 };
// The tab bar's panel toggles (herdr's tabs mod draws Files/Changes at these sizes).
export const PANEL_BUTTON = { h: 26, px: 8, gap: 5, icon: 14 };
export const ROW_H = { sidebar: 40, sidebarTitleOnly: 28, diff: 20 };
export const BREAKPOINT = { compact: 560, twoLine: 200 };
export const LAYOUT = { colMin: 260, colMax: 400, titleLh: 18, headerH: 52, msgMaxW: 480, bodyMaxH: 420, titleMaxH: 40, filterLabelW: 56, monoEm: 0.62, gutterDigits: 5, statusW: 14, cardTitleMaxH: 36 };
export const MONO = { charPx: Math.ceil(TEXT.caption * LAYOUT.monoEm), gutterDigits: LAYOUT.gutterDigits, gutter: Math.ceil(TEXT.caption * LAYOUT.monoEm) * LAYOUT.gutterDigits };
