/**
 * CSS for the web board from the sources the herdr app uses: colour variables from a herdr-app theme
 * JSON (ai/herdr/app/themes/<name>.generated.json, which bin/local/generate-herdr-app-themes renders
 * from ai/herdr/app/palettes/otto.toml and the brand's tokens), and size, radius and type-role
 * variables from _lib/tokens.js (which reads _lib/brand.generated.js). Computed by the server at
 * start, never committed. Pure.
 */
import { DIFF, STATUS } from '../../app/mods/_lib/status.js';

// The width under which the header drops its "updated" text; it also lets the remaining controls
// wrap, which they do only if they still do not fit. Web-only: the brand defines no header
// breakpoint and the desktop board has no such header.
export const HEADER_WRAP_PX = 760;

// DESIGN.md: app roles are League Spartan, code is League Mono.
// ceiling: no font files ship (they live in the brand package, not in a checkout, and the release is
// offline), so a browser without the faces falls back to system ones. Upgrade trigger: once the brand
// fonts are vendored into the repo or the release builder may fetch, add @font-face rules here.
export const FONT_SANS = '"League Spartan", -apple-system, system-ui, "Segoe UI", sans-serif';
export const FONT_MONO = '"League Mono", ui-monospace, "SF Mono", Menlo, monospace';

const varName = (k) => `--${String(k).replace(/_/g, '-')}`;

export function themeVars(theme) {
  const out = [];
  for (const k of ['background', 'foreground', 'selection', 'cursor']) {
    if (typeof theme?.[k] === 'string') out.push(`${varName(k)}:${theme[k]}`);
  }
  (theme?.ansi ?? []).forEach((c, i) => out.push(`--ansi${i}:${c}`));
  for (const [k, v] of Object.entries(theme?.colors ?? {})) out.push(`${varName(k)}:${v}`);
  return out.join(';');
}

// The status kinds DESIGN.md gives a colour, mapped to theme fields from _lib/status.js
// (the palette puts danger on ANSI 1 and warn on ANSI 3).
const STATUS_VARS = [
  ...Object.entries(STATUS).map(([k, s]) => `--status-${k}:var(--${s.field})`),
  ...Object.entries(DIFF).map(([k, f]) => `--diff-${k}:var(--${f})`),
].join(';');
// DESIGN.md --ow-scrim: block at 50 % on light, canvas at 70 % on dark.
// ceiling: the theme JSON carries no block colour; the brand's light block is its ink, so light uses
// --foreground. Upgrade trigger: once the palette exposes block (or a scrim), read it instead.
const SCRIM_LIGHT = '--scrim:color-mix(in srgb, var(--foreground) 50%, transparent)';
const SCRIM_DARK = '--scrim:color-mix(in srgb, var(--canvas) 70%, transparent)';

export function themeCss(light, dark) {
  const l = `${themeVars(light)};${STATUS_VARS};${SCRIM_LIGHT}`;
  const d = `${themeVars(dark)};${STATUS_VARS};${SCRIM_DARK}`;
  return `:root,[data-theme="light"]{color-scheme:light;${l}}\n`
    + `[data-theme="dark"]{color-scheme:dark;${d}}\n`
    + `@media (prefers-color-scheme: dark){:root:not([data-theme]){color-scheme:dark;${d}}}\n`;
}

// tokens.js FILL values are hex alpha suffixes; color-mix wants a percentage.
export function fillPercent(hexAlpha) {
  return Math.round((parseInt(hexAlpha, 16) / 255) * 100);
}

export function tokensCss({ SP, TYPE, RADIUS, CONTROL, ROW_H, FILL, LAYOUT }) {
  const v = [`--font-sans:${FONT_SANS}`, `--font-mono:${FONT_MONO}`];
  for (const [k, n] of Object.entries(SP)) v.push(`--sp-${k}:${n}px`);
  // One CSS font shorthand per DESIGN.md app role: `font: var(--font-body)`.
  for (const [role, r] of Object.entries(TYPE)) {
    v.push(`--font-${role}:${r.weight} ${r.size}px/${r.lh}px var(${r.mono ? '--font-mono' : '--font-sans'})`);
  }
  for (const [k, n] of Object.entries(RADIUS)) v.push(`--radius-${k}:${n}px`);
  for (const [k, hex] of Object.entries(FILL)) v.push(`--fill-${k}:${fillPercent(hex)}%`);
  v.push(
    `--control-h:${CONTROL.h}px`, `--control-h-dense:${CONTROL.hDense}px`, `--control-px:${CONTROL.px}px`,
    `--row-h-list:${ROW_H.list}px`, `--row-h-list-dense:${ROW_H.listDense}px`, `--row-h-sidebar:${ROW_H.sidebar}px`, `--row-h-diff:${ROW_H.diff}px`,
    `--col-min:${LAYOUT.colMin}px`, `--col-max:${LAYOUT.colMax}px`, `--header-h:${LAYOUT.headerH}px`,
    `--card-title-max-h:${LAYOUT.cardTitleMaxH}px`,
    `--title-max-h:${LAYOUT.titleMaxH}px`, `--msg-max-w:${LAYOUT.msgMaxW}px`, `--status-w:${LAYOUT.statusW}px`,
    `--body-max-h:${LAYOUT.bodyMaxH}px`, `--icon-size:${LAYOUT.iconSize}px`,
    // DESIGN.md --ow-rule-selected / --ow-focus-width / --ow-focus-offset; brand.generated.js does not
    // carry them and the mod hard-codes the same 2px (border_l_2).
    '--rule-selected:2px', '--focus-width:2px', '--focus-offset:2px',
  );
  return `:root{${v.join(';')}}\n`;
}

// Media queries cannot read custom properties, so the widths reach CSS through the <link media>
// attributes the server renders into index.html. compact.js viewOf is `width < compact`; DESIGN.md's
// wide pane (board plus a detail pane) starts at BRAND.pane.wide.
export function mediaQueries({ BREAKPOINT }, brand) {
  return {
    compact: `(max-width: ${BREAKPOINT.compact - 0.02}px)`,
    wrap: `(max-width: ${HEADER_WRAP_PX}px)`,
    wide: `(min-width: ${brand.pane.wide}px)`,
  };
}
