# site

The public documentation site for otto-workbench, published to
https://otto-nation.github.io/otto-workbench/ by `.github/workflows/pages.yml`.

## Not a component

This directory is **not** an installable workbench component and must never gain a
`setup.sh`, `setup.conf`, or `steps.sh`. Component discovery in `lib/components.sh`
globs `*/steps.sh`, `*/*/steps.sh`, and `*/migrations`; those filenames are what make
a directory installable.

## Content

The site reads `docs/*.md` in place — it does not copy or transform them. Generator
scripts keep owning their `<!-- *-START -->` regions and need no knowledge of the site.
`docs/superpowers/` is gitignored and is excluded by the top-level-only `files: ['*.md']`
glob in `source.config.ts`.

`docs/` lives outside the Next.js project root (`site/`), which is the spec's open
question. The direct form, `dir: '../docs'`, generates `.source/` correctly but fails
once the app actually builds: Turbopack refuses to resolve any module path that
escapes `site/` ("Module not found", then the same rejection again for a raw
`site/content/docs -> ../../docs` symlink: "Symlink ... points out of the filesystem
root"). The fallback symlink is what's checked in (`dir: 'content/docs'`), paired with
`turbopack: { root: <repo root> }` in `next.config.mjs` — without that `root` override,
even the symlinked fallback is rejected the same way. With it, both the symlink and the
module boundary resolve against the repo root, which contains both `site/` and `docs/`.

## Fonts

`@otto-nation/brand` owns both faces and vendors them as variable woff2 loaded by
plain `@font-face`. This site declares no fonts and calls no `next/font` loader.
Provenance for each face is in that package's README.

## Local development

    npm install
    npm run dev

`npm run build` works offline. Both fonts are vendored inside `@otto-nation/brand`,
so nothing is fetched at build time and the published site makes no third-party
font request.

## Tests

    npm test

The `test` script is bare `node --test` on purpose. Node's runner recurses from the
working directory and already skips `node_modules/`, so every `*.test.mjs` under
`site/` is picked up by adding the file. Naming files explicitly has twice been the
cause of a suite that silently ran nothing — do not reintroduce a path list.

## Colors

`@otto-nation/brand/tokens.css` is the only place a hex literal belongs, and it lives
in that package, not here. Components reference `var(--ow-*)`;
`tests/site_palette_ssot.bats` fails the push if anything under `site/` grows a hex or
re-declares the palette, and `npm run check` (`otto-brand-check`, shipped with the
package) fails the build if a referenced token is not declared or if `@source` /
`transpilePackages` go missing.

`app/icon.svg` is the one exemption — Next's metadata convention reads it off disk as a
static file, so it cannot resolve a custom property. `icon-parity.test.mjs` keeps it
byte-identical to the mark the package ships.

The `--ow-block-*` group is the dark band used by the install block and the footer,
which stay dark in both themes and so cannot follow the light/dark ramp. The package
restates all four in `.dark`, lifting the band off the dark canvas rather than letting
it sink invisibly below it.
