/**
 * Design tokens for herdr-app mods. Pure ESM; no imports.
 * Density: rail < 160 ≤ compact < 560 ≤ board < 1100 ≤ wide.
 */

export const SP = { xs: 4, sm: 8, md: 12, lg: 16, xl: 24 };
export const TEXT = { caption: 11, small: 12, body: 13, title: 14 };
export const RADIUS = { control: 6, card: 8, panel: 12 };
export const CONTROL_H = { board: 28, compact: 24 };
export const ROW_H = { sidebar: 40, sidebarTitleOnly: 28 };
export const BREAKPOINT = { rail: 160, compact: 560, wide: 1100 };

export function densityOf(width) {
  if (width === undefined) return "board";
  if (width < BREAKPOINT.rail) return "rail";
  if (width < BREAKPOINT.compact) return "compact";
  if (width < BREAKPOINT.wide) return "board";
  return "wide";
}
