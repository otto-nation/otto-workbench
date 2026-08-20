import { fileURLToPath } from 'node:url';
import { createMDX } from 'fumadocs-mdx/next';

/** @type {import('next').NextConfig} */
const config = {
  output: 'export',
  basePath: '/otto-workbench',
  // A static export on GitHub Pages serves directories by resolving `index.html`
  // inside them, not the sibling `<slug>.html`. Without this, `next export` emits
  // both `docs/<slug>.html` and `docs/<slug>/` (RSC payloads only, no index.html),
  // and a request for `/docs/<slug>/` 404s instead of falling back to the sibling.
  trailingSlash: true,
  images: { unoptimized: true },
  reactStrictMode: true,
  // The package ships raw .tsx with no build step — Tailwind's scanner has to
  // read the source anyway, so a compiled artifact would mean shipping source
  // and build output both. Next has to compile it here instead.
  transpilePackages: ['@otto-nation/brand'],
  // `content/docs` is a symlink to `../../docs` (outside `site/`). Without an explicit
  // root, Turbopack treats `site/` as the filesystem boundary and refuses to follow a
  // symlink that resolves outside it ("Symlink ... points out of the filesystem root").
  // Pointing root at the repo root, which contains both `site/` and `docs/`, fixes this.
  turbopack: { root: fileURLToPath(new URL('..', import.meta.url)) },
};

export default createMDX()(config);
