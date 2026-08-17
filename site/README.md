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

- **League Spartan** — via `next/font/google`, downloaded and self-hosted at build time.
- **League Mono** — vendored at `app/fonts/LeagueMonoVariable.woff2`, from
  https://github.com/theleagueof/league-mono (variable, weight + width axes). Not on
  Google Fonts yet; when it lands there, move it to `next/font/google` and delete the
  vendored file.

Both are SIL OFL. The published site makes no third-party font request.

## Local development

    npm install
    npm run dev

`npm run build` needs network access — `next/font/google` fetches League Spartan at
build time. Offline builds fail on fonts alone.
